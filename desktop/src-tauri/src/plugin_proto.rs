//! yibao-plugin:// 自定义协议:运行时从 plugins/ 目录只读服务 module 面板静态资源。
//!
//! 安全模型:
//! - 路径防穿越:pid 白名单字符 + 拒绝非 Normal 路径段(含 `..`)+ canonicalize 后前缀校验
//! - CSP 注入(仅 HTML):default-src 'none',脚本/样式/图片/字体仅放行 yibao-plugin: 与 data:,
//!   connect-src 'none' 断网络——插件对外通信只有 postMessage 桥(父侧再过 api.toml 白名单)
//! - Access-Control-Allow-Origin: *:sandbox iframe 是无 allow-same-origin 的透明源(opaque origin),
//!   ES module 加载走 CORS 模式,没有本头插件自己的 bundle 会被源校验拦下(与 vscode webview 同手法)
//! - /__yibao__/ 为宿主保留路径(桥 SDK / 共享 Vue runtime),由 include_bytes! 内嵌,不读盘

use std::borrow::Cow;
use std::path::{Component, Path, PathBuf};
use tauri::http::{Request, Response};

const BRIDGE_JS: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/../src/shared/bridge.js"));
const VUE_JS: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/resources/sdk/vue.esm-browser.js"));

const IMPORTMAP: &str = "<script type=\"importmap\">{\"imports\":{\"vue\":\"/__yibao__/vue.esm-browser.js\"}}</script>";
const BRIDGE_TAG: &str = "<script src=\"/__yibao__/bridge.js\"></script>";

/// CSP 按 pid 收窄到本插件源;/__yibao__/ SDK 在任意 pid 下可服务,故同属本源可达。
/// form-action 禁表单外发;connect-src 'none' 断网络(XHR/fetch/ws)。
/// 源列表用「host 精确 + scheme 兜底」双形式:yibao-plugin://<pid> 精确收窄,
/// yibao-plugin: 兜底——WKWebView 对自定义协议 host-source 的匹配在部分版本不生效,
/// 漏掉兜底会导致面板的 app.js/style.css 被 CSP 误拦(空白/黑屏)。
/// fun 插件(R4 内嵌播放)额外放行:img-src https:(视频封面)、media-src https:(音频流)、
/// frame-src 官方嵌入播放器域(B站 player.bilibili.com / 网易云 music.163.com)——
/// 只对 fun 生效,其它插件维持禁网络现状;connect-src 一律 'none'(数据仍走桥,不做任意外联)。
fn csp_meta(pid: &str) -> String {
    let img_extra = if pid == "fun" { " https:" } else { "" };
    let embed_extra = if pid == "fun" {
        // 内嵌播放器(播放/听)+ 站内搜索页与视频页(B站搜索页/视频页无 X-Frame-Options,可 iframe 嵌入)
        "; media-src https:; frame-src https://player.bilibili.com https://music.163.com https://search.bilibili.com https://www.bilibili.com"
    } else {
        ""
    };
    let self_src = format!("yibao-plugin://{pid} yibao-plugin:");
    format!(
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src {self_src} 'unsafe-inline'; style-src {self_src} 'unsafe-inline'; img-src {self_src}{img_extra} data:; font-src {self_src} data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'{embed_extra}\">"
    )
}

fn mime_for(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase().as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" | "map" => "application/json; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "txt" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

fn valid_pid(pid: &str) -> bool {
    !pid.is_empty() && pid.len() <= 64 && pid.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_')
}

/// 拼接并校验插件内相对路径:拒绝 `..`/根路径等非常规段;canonicalize 后必须仍在插件目录内。
/// 文件不存在 → None(调用方回 404)。
fn resolve_plugin_path(root: &Path, pid: &str, rel: &str) -> Option<PathBuf> {
    if !valid_pid(pid) || rel.is_empty() {
        return None;
    }
    let rel_path = Path::new(rel);
    if !rel_path.components().all(|c| matches!(c, Component::Normal(_))) {
        return None;
    }
    let base = root.join(pid).canonicalize().ok()?;
    let full = base.join(rel_path).canonicalize().ok()?;
    if full.starts_with(&base) { Some(full) } else { None }
}

/// 最小 %XX 解码(静态资源路径只需这个);非法序列/非 UTF-8 → None(按 404 处理)。
fn pct_decode(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 3 > bytes.len() {
                return None;
            }
            let h = u8::from_str_radix(s.get(i + 1..i + 3)?, 16).ok()?;
            out.push(h);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

/// 在 <head> 之后注入 CSP + importmap + 桥 SDK(无 <head> 则放最前)——与 WebviewPanel srcdoc 注入同手法。
/// 在原串上做 ASCII 大小写不敏感查找:to_lowercase() 会改变字节长度,索引回切原串会错位/越界。
fn inject_sdk(html: &str, pid: &str) -> String {
    let tag = format!("{}{}{}", csp_meta(pid), IMPORTMAP, BRIDGE_TAG);
    let bytes = html.as_bytes();
    let needle = b"<head>";
    let pos = bytes
        .windows(needle.len())
        .position(|w| w.eq_ignore_ascii_case(needle));
    match pos {
        // 命中段是纯 ASCII,索引必落在字符边界上;保留原串标签大小写
        Some(i) => format!("{}{}{}", &html[..i + needle.len()], tag, &html[i + needle.len()..]),
        None => format!("{tag}{html}"),
    }
}

fn respond(status: u16, mime: &str, body: Cow<'static, [u8]>) -> Response<Cow<'static, [u8]>> {
    Response::builder()
        .status(status)
        .header("Content-Type", mime)
        .header("Access-Control-Allow-Origin", "*")
        .header("Cache-Control", "no-store") // mtime 版本号热加载的前提:响应不许缓存
        .body(body)
        .expect("protocol response build")
}

pub fn handle(request: &Request<Vec<u8>>, plugins_root: &Path) -> Response<Cow<'static, [u8]>> {
    let pid = request.uri().host().unwrap_or("");
    let raw = request.uri().path().trim_start_matches('/');
    if raw.starts_with("__yibao__/") {
        return match raw {
            "__yibao__/bridge.js" => respond(200, "text/javascript; charset=utf-8", Cow::Borrowed(BRIDGE_JS)),
            "__yibao__/vue.esm-browser.js" => respond(200, "text/javascript; charset=utf-8", Cow::Borrowed(VUE_JS)),
            _ => respond(404, "text/plain; charset=utf-8", Cow::Borrowed(&b"not found"[..])),
        };
    }
    let path = pct_decode(raw).and_then(|rel| resolve_plugin_path(plugins_root, pid, &rel));
    let Some(path) = path else {
        return respond(404, "text/plain; charset=utf-8", Cow::Borrowed(&b"not found"[..]));
    };
    let mime = mime_for(&path);
    match std::fs::read(&path) {
        Ok(bytes) if mime.starts_with("text/html") => match String::from_utf8(bytes) {
            Ok(html) => respond(200, mime, Cow::Owned(inject_sdk(&html, pid).into_bytes())),
            Err(_) => respond(500, "text/plain; charset=utf-8", Cow::Borrowed(&b"bad html"[..])),
        },
        Ok(bytes) => respond(200, mime, Cow::Owned(bytes)),
        Err(_) => respond(404, "text/plain; charset=utf-8", Cow::Borrowed(&b"not found"[..])),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(uri: &str) -> Request<Vec<u8>> {
        Request::builder().uri(uri).body(Vec::new()).unwrap()
    }

    fn fixture_root() -> PathBuf {
        let root = std::env::temp_dir().join(format!("yibao-proto-test-{}", std::process::id()));
        let dist = root.join("demo/panel/dist");
        std::fs::create_dir_all(&dist).unwrap();
        std::fs::write(dist.join("index.html"), "<html><head></head><body>hi</body></html>").unwrap();
        std::fs::write(dist.join("app.js"), "console.log(1)").unwrap();
        root
    }

    #[test]
    fn rejects_traversal_and_bad_pid() {
        let root = fixture_root();
        assert!(resolve_plugin_path(&root, "demo", "../secret").is_none());
        assert!(resolve_plugin_path(&root, "demo", "a/../../b").is_none());
        assert!(resolve_plugin_path(&root, "../etc", "panel/dist/index.html").is_none());
        assert!(resolve_plugin_path(&root, "", "panel/dist/index.html").is_none());
        assert!(resolve_plugin_path(&root, "Demo", "x").is_none()); // 大写不收(WKWebView host 全小写,与插件 id 一致)
        assert!(resolve_plugin_path(&root, "demo", "panel/dist/index.html").is_some());
        assert!(resolve_plugin_path(&root, "demo", "missing.js").is_none());
    }

    #[test]
    fn mime_mapping() {
        assert_eq!(mime_for(Path::new("a/b.html")), "text/html; charset=utf-8");
        assert_eq!(mime_for(Path::new("a/b.JS")), "text/javascript; charset=utf-8");
        assert_eq!(mime_for(Path::new("a/b.woff2")), "font/woff2");
        assert_eq!(mime_for(Path::new("a/b.bin")), "application/octet-stream");
    }

    #[test]
    fn injects_after_head_or_prepends() {
        let out = inject_sdk("<html><head><title>t</title></head><body/></html>", "demo");
        let head = out.find("<head>").unwrap();
        let csp = out.find("Content-Security-Policy").unwrap();
        let map = out.find("importmap").unwrap();
        let bridge = out.find("/__yibao__/bridge.js").unwrap();
        assert!(head < csp && csp < map && map < bridge); // 桥在插件脚本前生效
        let bare = inject_sdk("<div/>", "demo");
        assert!(bare.find("Content-Security-Policy").unwrap() < bare.find("<div/>").unwrap());
    }

    #[test]
    fn injects_with_non_ascii_before_head() {
        // 回归:to_lowercase 变长字符(İ)在 <head> 前时索引不得错位/越界
        let out = inject_sdk("<!-- İ 注释 --><html><HEAD><title>t</title></HEAD><body/></html>", "demo");
        let head_end = out.find("<HEAD>").unwrap() + 6;
        // 紧跟 <HEAD> 之后(csp_meta(pid) 以 `<meta http-equiv="` 开头)
        assert!(out[head_end..].starts_with("<meta http-equiv=\"Content-Security-Policy"));
        assert!(out.contains("<body/>"));
        assert!(out.contains("<!-- İ 注释 -->"));
    }

    #[test]
    fn pct_decode_basics() {
        assert_eq!(pct_decode("a%20b/c.js").as_deref(), Some("a b/c.js"));
        assert!(pct_decode("a%2f..%2f").is_some()); // 解码后含 .. 由 resolve_plugin_path 拦
        assert!(pct_decode("%zz").is_none());
        assert!(pct_decode("%4").is_none());
    }

    #[test]
    fn csp_is_scoped_per_pid() {
        let root = fixture_root();
        let r = handle(&req("yibao-plugin://demo/panel/dist/index.html"), &root);
        let body = String::from_utf8_lossy(r.body()).into_owned();
        // 双源:host 精确 + scheme 兜底(兜底防 WKWebView 对自定义协议 host-source 不匹配而误拦子资源)
        assert!(body.contains("script-src yibao-plugin://demo yibao-plugin: 'unsafe-inline'"));
        assert!(body.contains("style-src yibao-plugin://demo yibao-plugin: 'unsafe-inline'"));
        assert!(body.contains("form-action 'none'"));
        // 非 fun 插件不获得内嵌放宽(connect-src 一律禁网,img/media/frame 不放开)
        assert!(!body.contains("frame-src"));
        assert!(!body.contains("media-src"));
        assert!(!body.contains("img-src yibao-plugin://demo https:"));
    }

    #[test]
    fn csp_embed_extra_only_for_fun() {
        // fun 娱乐面板内嵌播放:放行 B站/网易云官方嵌入播放器 + 站内搜索页/视频页 + 媒体流 + 封面图
        let fun = csp_meta("fun");
        assert!(fun.contains("frame-src https://player.bilibili.com https://music.163.com https://search.bilibili.com https://www.bilibili.com"));
        assert!(fun.contains("media-src https:"));
        assert!(fun.contains("img-src yibao-plugin://fun yibao-plugin: https: data:"));
        assert!(fun.contains("connect-src 'none'")); // 数据仍走桥,不开放 fetch
        let demo = csp_meta("demo");
        assert!(!demo.contains("frame-src"));
        assert!(!demo.contains("media-src"));
        assert!(!demo.contains("img-src yibao-plugin://demo https:"));
    }

    #[test]
    fn serves_sdk_and_injects_html() {
        let root = fixture_root();
        let r = handle(&req("yibao-plugin://demo/__yibao__/bridge.js"), &root);
        assert_eq!(r.status(), 200);
        assert!(String::from_utf8_lossy(r.body()).contains("window.yibao"));

        let r = handle(&req("yibao-plugin://demo/panel/dist/index.html"), &root);
        assert_eq!(r.status(), 200);
        let body = String::from_utf8_lossy(r.body()).into_owned();
        assert!(body.contains("Content-Security-Policy"));
        assert!(body.contains("/__yibao__/bridge.js"));
        assert_eq!(r.headers().get("Access-Control-Allow-Origin").unwrap(), "*");
        assert_eq!(r.headers().get("Cache-Control").unwrap(), "no-store");

        let r = handle(&req("yibao-plugin://demo/panel/dist/app.js"), &root);
        assert_eq!(r.status(), 200);
        assert!(!String::from_utf8_lossy(r.body()).contains("Content-Security-Policy")); // 只注入 HTML

        assert_eq!(handle(&req("yibao-plugin://demo/../etc/passwd"), &root).status(), 404);
        assert_eq!(handle(&req("yibao-plugin://demo/missing.js"), &root).status(), 404);
        assert_eq!(handle(&req("yibao-plugin://evil/__yibao__/nope.js"), &root).status(), 404);
    }
}
