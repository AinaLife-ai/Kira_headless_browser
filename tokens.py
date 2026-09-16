"""扩展接入令牌的签发与校验。

KiraAI 的 WebUI 用 JWT（HS256）鉴权，签名密钥来自 ``JWT_SECRET`` 环境变量或
``<data_dir>/.jwt_secret`` 文件。插件与 WebUI 跑在同一个进程里，因此可以直接复用
``webui.utils`` 的签发函数，不需要手搓 JWT。

之所以要自己签：WebUI 通过 ``/api/auth/token-login`` 换到的 JWT 只有 5 天有效期，
而扩展应该一次配置长期有效。这里签 30 天，并支持一键重新生成。
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from core.logging_manager import get_logger
from core.utils.path_utils import get_data_path

logger = get_logger("browser_bridge", "cyan")

#: 扩展令牌的有效期（天）。``None`` 表示**不设过期** —— 令牌一直有效，
#: 直到用户在面板里点「重新生成」（旧令牌当场作废）。
#:
#: 之所以默认不设过期：这是一台本机服务，令牌只连 127.0.0.1，
#: 用户明确要求别每隔一个月就得重新粘贴一次。失效手段改成了"主动作废"，
#: 比"被动等它过期"更符合实际用法。
DEFAULT_TOKEN_DAYS: Optional[int] = None

#: 令牌落盘位置（插件数据目录）
_TOKEN_FILE_NAME = "extension_token.txt"

#: 令牌世代号落盘文件名。
#:
#: ⚠️ **这个文件现在只是留痕，不再参与校验。**
#:
#: 曾经的设计是把世代号混进 ``tv`` 计算输入，靠"世代号一变、旧令牌 tv 全对不上"
#: 来作废令牌。那个做法在真实链路上是错的：服务端只认
#: ``tv == sha256(app.state.access_token)[:16]``，掺了私货之后**连新令牌**
#: 都会被判 "Session invalidated by an access-token change"，扩展直接连不上。
#:
#: 现在作废由 WS 端点上的 jti 闸门负责（:func:`is_current_token`），
#: 世代号只用来记录"第几代"，方便排查。
_ROTATION_FILE_NAME = "token_generation.txt"

#: "不过期"用的远期时间戳偏移（100 年）。理由见 :func:`issue_token`。
_NEVER_DELTA = timedelta(days=36500)


# ─── 读取 WebUI 的鉴权上下文 ────────────────────────────────────────────────

def read_access_token(data_dir: Optional[Path] = None) -> Optional[str]:
    """从 ``<data_dir>/webui.json`` 读取 WebUI 的 access_token。

    JWT 里要带一个 access_token 的指纹（``tv`` claim），access_token 轮换时
    旧会话会立刻失效。这里必须拿到与服务端内存中一致的值。

    注意：``webui.app.KiraWebUI`` 在 ``--disable-webui-auth`` 模式下会把内存中的
    access_token 硬编码成字符串 ``"disabled"``，而配置文件里可能存的是另一个值。
    两种情况的判定交给 :func:`detect_auth_mode`。
    """
    base = data_dir or get_data_path()
    config_path = base / "webui.json"
    try:
        if not config_path.is_file():
            return None
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("access_token")
        return str(token) if token else None
    except Exception as e:
        logger.warning(f"读取 webui.json 失败: {e}")
        return None


def detect_auth_mode(data_dir: Optional[Path] = None) -> Tuple[str, Optional[str]]:
    """判定当前服务的鉴权模式。

    Returns:
        ``(auth_mode, access_token)``

        - ``auth_mode`` 取 ``"enabled"`` 或 ``"disabled"``，对应 JWT 的
          ``auth_mode`` claim。服务端会用这个值比对，写错令牌会被判 401
          （见 webui.utils.verify_session_token）。
        - ``access_token`` 是用于计算 ``tv`` 指纹的令牌值；``disabled`` 模式下
          固定为 ``"disabled"``。

    判 ``disabled`` 只有一个依据：配置文件里**明确写着**保留哨兵 ``"disabled"``
    （``--disable-webui-auth`` 模式）。``webui.json`` 不存在或读不出来时，
    不能当成 disabled —— 那只是"没读到"，此时应该按默认的 enabled 处理。

    这条区分很要紧：读一次失败就判 disabled 的话，签出来的令牌
    ``auth_mode="disabled"``，而真实服务端是 enabled，于是握手被 4003 拒掉，
    用户怎么重连都连不上。宁可签一枚"enabled 但 tv 可能过期"的令牌
    （服务端会说 tv 不匹配），也不要签一枚 auth_mode 就错的令牌。
    """
    base = data_dir or get_data_path()

    token = read_access_token(base)
    if token == "disabled":
        # 配置里显式写了保留哨兵：服务端内存里的 access_token 就是 "disabled"
        return "disabled", "disabled"

    if not token:
        # 文件缺失/读不出来：按 enabled + 空 token 处理（宁可失败可见，不可静默降级）
        logger.warning(
            f"未能从 {base / 'webui.json'} 读到 access_token，"
            f"将按 enabled 模式签发（若服务端确实关闭了鉴权，请检查该文件）"
        )
        return "enabled", ""

    return "enabled", token


# ─── 令牌世代号（作废机制） ──────────────────────────────────────────────────

def rotation_file_path(data_dir: Optional[Path] = None) -> Path:
    base = data_dir or get_data_path()
    return base / "plugin_data" / "kira_browser_bridge" / _ROTATION_FILE_NAME


def load_rotation(data_dir: Optional[Path] = None) -> Optional[str]:
    """读取当前令牌世代号。没有就返回 None（表示"沿用 access_token 指纹"）。"""
    path = rotation_file_path(data_dir)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() or None
    except Exception as e:
        logger.warning(f"读取令牌世代号失败: {e}")
    return None


def rotate(data_dir: Optional[Path] = None) -> str:
    """换一个全新的令牌世代号，并**重新签发**当前这一枚令牌。

    ⚠️ 不要再写"这让所有旧令牌立即失效" —— **实际不是这样**：

    * 服务端只校验签名/有效期/auth_mode/``tv``，而 ``tv`` 是
      ``sha256(app.state.access_token)[:16]``，**与世代号无关**。
      （曾经想往 tv 里掺世代号，结果连新令牌一起被拒，已废弃。）
    * 所以光改世代号，旧令牌在服务端眼里**照样合法**。

    真正让旧令牌失效的是 ``is_current_token()`` 里的 **jti 闸门**：
    重新签发会写入新的 jti，旧令牌的 jti 与当前不相等 → 被拒。
    世代号的作用是记录"重新生成过"这件事，供面板/诊断使用。
    """
    value = secrets.token_hex(8)
    path = rotation_file_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception as e:
        logger.error(f"写入令牌世代号失败: {e}")
    return value


def _fingerprint_input(access_token: str, data_dir: Optional[Path] = None) -> str:
    """算 ``tv`` 用的输入串。

    ⚠️ **必须是 access_token 本身，不能掺任何插件自己的东西。**

    试过在末尾拼一个"世代号"来作废旧令牌 —— 那是错的：服务端
    ``verify_session_token`` 校验的是
    ``tv == sha256(app.state.access_token)[:16]``，它不知道我们掺了什么，
    结果**连新令牌都会被判不匹配**（"Session invalidated by an access-token
    change"），扩展根本连不上。作废只能走 :func:`is_current_token` 那条路。
    """
    return access_token


def token_tv(access_token: str, data_dir: Optional[Path] = None) -> str:
    """当前有效的 ``tv`` 指纹（与 KiraAI 其它会话令牌算法完全一致）。"""
    from webui.utils import _access_token_fingerprint
    return _access_token_fingerprint(_fingerprint_input(access_token, data_dir))


def current_jti(data_dir: Optional[Path] = None) -> Optional[str]:
    """当前**唯一有效**的那枚令牌的 jti（从落盘令牌里读）。"""
    token = load_saved_token(data_dir)
    if not token:
        return None
    return token_jti(token)


def token_jti(token: Optional[str]) -> Optional[str]:
    """从任意一枚令牌里取出 jti。"""
    if not token:
        return None
    try:
        import jwt as _jwt
        return _jwt.decode(token, options={"verify_signature": False}).get("jti")
    except Exception:
        return None


def is_current_token(token: Optional[str], data_dir: Optional[Path] = None) -> bool:
    """这枚令牌是不是"当前这一枚"？（WS 端点的闸门）

    ⚠️ 为什么必须有这个函数（踩过的坑，别再删）：

    服务端的 ``verify_session_token`` 只校验签名、有效期、auth_mode 和
    ``tv == sha256(app.state.access_token)[:16]`` —— 它**没有吊销列表**，
    所以「重新生成」之后旧令牌在服务端眼里依然合法，会照常连上来。
    （原本想靠往 tv 里掺世代号解决，结果连新令牌一起被拒，已废弃。）

    因此「重新生成 → 旧令牌立即失效」只能由插件自己把关：
    框架握手通过之后，WS 端点再检查一次 jti 是否等于当前那枚。

    判定顺序：
    1. 两边都有 jti → 必须相等
    2. 令牌没带 jti（0.1.x/1.0.0 签的老令牌）→ 退回比对 ``tv`` 与当前
       access_token 指纹。既不会把老令牌一律放行（那样等于没有闸门），
       也不会把它们一律拒绝（那会把用户锁在门外）
    3. 两边都没有 jti → 放行，交给框架的 auth 依赖处理
    """
    if not token:
        return True

    incoming_jti = token_jti(token)
    current = current_jti(data_dir)
    if incoming_jti and current:
        return incoming_jti == current
    if incoming_jti and not current:
        # 磁盘上没有令牌可对照（还没签发过）—— 保守放行，框架的握手校验仍在
        return True

    if current and not incoming_jti:
        # 当前那枚**有** jti，而进来的这枚没有 → 它不可能是"当前"这枚，
        # 只可能是更早签发的。用户点过「重新生成」后它必须失效，
        # 不能因为 tv 相同就放行（tv 依赖的是 access_token，重新生成不改它）。
        logger.info("拒绝无 jti 的旧令牌：当前令牌已带 jti，说明该令牌早于最近一次轮换")
        return False

    # 老令牌（无 jti）：退回 tv 指纹比对
    try:
        import jwt as _jwt
        payload = _jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return True                      # 解不开的交给框架去拒
    try:
        _, access_token = detect_auth_mode(data_dir)
        expected = token_tv(access_token, data_dir)
    except Exception:
        return True
    if payload.get("tv") and payload.get("tv") != expected:
        logger.info("老令牌（无 jti）的 tv 与当前 access_token 指纹不符，拒绝")
        return False
    return True


# ─── 令牌签发 ────────────────────────────────────────────────────────────────

def issue_token(days: Optional[int] = DEFAULT_TOKEN_DAYS,
                data_dir: Optional[Path] = None) -> str:
    """签发一枚供浏览器扩展使用的 JWT。

    Args:
        days: 有效期天数；``None`` 或 ``<= 0`` 表示**不设过期**（默认）。
        data_dir: 数据目录，测试用。

    Raises:
        RuntimeError: 无法导入 webui.utils（说明插件没有跑在 KiraAI 进程内）。
    """
    try:
        from webui.utils import _access_token_fingerprint, _create_jwt_token
    except Exception as e:  # pragma: no cover - 只在异常环境触发
        raise RuntimeError(f"无法导入 webui.utils，插件需运行在 KiraAI 进程内: {e}") from e

    auth_mode, access_token = detect_auth_mode(data_dir)

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "admin",
        "auth_mode": auth_mode,
        # 绑定「access_token + 当前令牌世代号」：access_token 轮换，
        # 或者用户在面板点「重新生成」，旧令牌都会立刻失效。
        "tv": _access_token_fingerprint(_fingerprint_input(access_token, data_dir)),
        # 自定义标记，便于排查是谁签的
        "src": "browser_bridge",
        # jti / iat 让每次签发都是**唯一**的。
        #
        # 少了它们会出一个很难解释的现象：JWT 载荷里的时间字段是秒级精度，
        # 同一秒内连续签两次会得到完全相同的字符串 —— 用户点「重新生成」，
        # 令牌一字不变，看起来就像按钮没生效。
        # jti 顺带也是审计线索：能分辨"这枚令牌是哪次签的"。
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
    }

    # 有效期：days 为 None/<=0 时用远期时间戳代替"不过期"。
    #
    # 为什么不干脆不带 exp：框架的 _create_jwt_token 在不传 expires_delta 时
    # 会自己补一个 **5 天** 的默认值（不是"不过期"），而服务端的
    # _verify_jwt_token 走的是 pyjwt 默认校验。与其去拼一个"没有 exp"的令牌、
    # 赌服务端能接受，不如给一个实际永远不会到的日期 —— 用的还是框架原函数，
    # 兼容性没有任何疑问。
    expires_delta = timedelta(days=days) if days and days > 0 else _NEVER_DELTA
    token = _create_jwt_token(payload, expires_delta=expires_delta)
    life = f"{days} 天" if days and days > 0 else "不设过期（重新生成即作废）"
    logger.info(f"已签发扩展令牌 (mode={auth_mode}, {life}, jti={payload['jti'][:8]})")
    return token


def token_expiry(days: Optional[int] = DEFAULT_TOKEN_DAYS) -> Optional[str]:
    """令牌过期时间的可读字符串；不设过期时返回 None。"""
    if not days or days <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M UTC")


# ─── 令牌持久化 ──────────────────────────────────────────────────────────────

def token_file_path(data_dir: Optional[Path] = None) -> Path:
    base = data_dir or get_data_path()
    return base / "plugin_data" / "kira_browser_bridge" / _TOKEN_FILE_NAME


def load_saved_token(data_dir: Optional[Path] = None) -> Optional[str]:
    """读取上次生成的扩展令牌。"""
    path = token_file_path(data_dir)
    try:
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            return value or None
    except Exception as e:
        logger.warning(f"读取已保存令牌失败: {e}")
    return None


def save_token(token: str, data_dir: Optional[Path] = None) -> Path:
    """把令牌写入插件数据目录（权限尽量收紧）。

    ⚠️ 不能"先写入最终路径、再 chmod"：那样在写入与 chmod 之间有一小段
    窗口，文件是**默认权限**（通常 0644，同机其他用户可读）；
    而且中途被杀掉时权限永远补不上。

    正确做法：
      1. 目录建成 **0o700**（只有自己可进）；
      2. 令牌写进**同目录**下的临时文件，**创建时就**是 0o600；
      3. `os.replace` **原子替换**到最终路径。
    这样任何时候别人都读不到中间态，也不存在"写一半"的坏文件。
    """
    path = token_file_path(data_dir)
    # 目录权限：0o700（已存在则只补权限，不报错）
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass  # Windows 上 chmod 基本无效，尽力而为

    fd = None
    tmp_path = None
    try:
        # 用 O_CREAT|O_EXCL 保证"创建时就带 0o600"，没有中间态
        fd, tmp_name = tempfile.mkstemp(prefix=".token_", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None          # fdopen 接管后不要再自己关
            f.write(token)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)   # 原子
        tmp_path = None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return path


def saved_token_is_usable(token: Optional[str],
                          data_dir: Optional[Path] = None) -> bool:
    """落盘的令牌现在还能用吗？

    这些情况下服务端会在握手阶段拒掉，必须重新签，否则面板会一直显示
    一枚看着正常、实际连不上的令牌，用户只能靠"连接失败"去猜：

    - 已经过期（本插件默认不设过期，但旧版本签的令牌可能带 exp）
    - 服务端 access_token 轮换过（``tv`` 指纹对不上）

    注意「用户在面板点过重新生成」不在这个列表里 —— 那枚被作废的令牌**本来
    就不是落盘这一枚**了（落盘的是新的），而旧令牌由 WS 端点的 jti 闸门拦住，
    见 :func:`is_current_token`。

    这里只做本地判断，不发任何请求；解不开（换了 JWT 密钥、格式不认识）
    就按不可用处理，重新签一枚不花什么代价。
    """
    if not token:
        return False

    try:
        import jwt as _jwt
    except Exception as e:  # pragma: no cover - 依赖缺失时保守处理
        logger.debug(f"无法导入 jwt，跳过已存令牌的有效性检查: {e}")
        return True

    try:
        payload = _jwt.decode(token, options={"verify_signature": False})
    except Exception as e:
        logger.info(f"已存令牌无法解析（{type(e).__name__}），将重新签发")
        return False

    exp = payload.get("exp")
    if exp is not None:
        try:
            if datetime.now(timezone.utc).timestamp() >= float(exp):
                logger.info("已存扩展令牌已过期，将重新签发")
                return False
        except (TypeError, ValueError):
            return False

    # tv 绑定当前 access_token：轮换后旧令牌立刻失效（与 KiraAI 其它会话一致）
    try:
        _, access_token = detect_auth_mode(data_dir)
        expected = token_tv(access_token, data_dir)
    except Exception as e:  # pragma: no cover
        logger.debug(f"无法计算 tv，跳过检查: {e}")
        return True
    if payload.get("tv") and payload.get("tv") != expected:
        logger.info("已存扩展令牌的 tv 与当前 access_token 指纹不一致，将重新签发")
        return False

    return True


def ensure_token(days: Optional[int] = DEFAULT_TOKEN_DAYS, force: bool = False,
                 data_dir: Optional[Path] = None) -> str:
    """取令牌：有、且还有效、且不强制刷新就复用，否则新签一枚并落盘。

    ``force=True`` 是「重新生成」的语义：签一枚**新 jti** 的令牌。
    新令牌一落盘，WS 端点的 jti 闸门就会把之前那枚挡在门外（见
    :func:`is_current_token`），所以旧令牌事实上当场作废。
    世代号只作留痕。
    """
    if not force:
        saved = load_saved_token(data_dir)
        if saved and saved_token_is_usable(saved, data_dir):
            return saved
    else:
        rotate(data_dir)

    token = issue_token(days=days, data_dir=data_dir)
    save_token(token, data_dir=data_dir)
    return token


def random_handshake_secret(length: int = 32) -> str:
    """生成一次性握手密钥（预留：可用于扩展首次配对的加固流程）。"""
    return secrets.token_urlsafe(length)
