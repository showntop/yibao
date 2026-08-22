//! 插件 manifest.toml 的轻量解析（行级、不引 toml 依赖）。
use serde_json::Value;

/// 解析插件 manifest.toml：顶层 id/name + 带 open 的 [[panel]] 条目（面板级入口，如素材库）。
/// 行级解析、不引 toml 依赖：顶层键只在任何 section 之前读（[[tool]] 里也有 id，不能误抓）；
/// [[panel]] 段内只取 name/label/open，无 open 的 panel 收成不了入口（detail/editor 需参数，本就不能直接开）。
pub fn parse_manifest(text: &str) -> Option<(String, String, Vec<Value>)> {
    // 取 `key = "值"` 的引号内内容：第一个引号到闭引号，之后的东西（如行尾注释）一律不混进值
    let val = |l: &str, key: &str| -> Option<String> {
        let rest = l.trim().strip_prefix(key)?;
        let rest = rest.trim_start().strip_prefix('=')?.trim_start();
        let rest = rest.strip_prefix('"')?;
        let end = rest.find('"')?;
        Some(rest[..end].to_string())
    };
    let mut id: Option<String> = None;
    let mut name: Option<String> = None;
    let mut panels: Vec<Value> = Vec::new();
    let mut seen_section = false;
    let mut in_panel = false;
    let mut pname = String::new();
    let mut plabel: Option<String> = None;
    let mut popen: Option<String> = None;
    // 只负责「收」：take 走字段（留下空态），不做重置赋值——重置由 section 分支显式做，EOF 后无人再读
    macro_rules! flush_panel {
        () => {
            if in_panel && !pname.is_empty() {
                if let Some(open) = popen.take() {
                    let label = plabel.take().filter(|s| !s.is_empty()).unwrap_or_else(|| pname.clone());
                    panels.push(serde_json::json!({
                        "name": std::mem::take(&mut pname), "label": label, "open": open,
                    }));
                }
            }
        };
    }
    for line in text.lines() {
        let l = line.trim();
        if l.starts_with('[') {
            flush_panel!();
            seen_section = true;
            in_panel = l == "[[panel]]";
            pname.clear();
            plabel = None;
            popen = None;
            continue;
        }
        if !seen_section {
            if id.is_none() {
                id = val(l, "id");
            }
            if name.is_none() {
                name = val(l, "name");
            }
            continue;
        }
        if in_panel {
            if pname.is_empty() {
                if let Some(v) = val(l, "name") {
                    pname = v;
                    continue;
                }
            }
            if plabel.is_none() {
                if let Some(v) = val(l, "label") {
                    plabel = Some(v);
                    continue;
                }
            }
            if popen.is_none() {
                if let Some(v) = val(l, "open") {
                    popen = Some(v);
                }
            }
        }
    }
    flush_panel!();
    Some((id?, name?, panels))
}

/// 解析插件 api.toml 中可暴露为 `/命令` 的方法（`direct = true` 且 `command = true` 的 [[method]]）。
/// 行级解析（同 manifest，不引 toml 依赖）：只取 name/handler，供前端动态生成插件命令。
pub fn parse_api_commands(text: &str) -> Vec<Value> {
    let val = |l: &str, key: &str| -> Option<String> {
        let rest = l.trim().strip_prefix(key)?;
        let rest = rest.trim_start().strip_prefix('=')?.trim_start();
        let rest = rest.strip_prefix('"')?;
        let end = rest.find('"')?;
        Some(rest[..end].to_string())
    };
    let mut out: Vec<Value> = Vec::new();
    let mut in_method = false;
    let mut name = String::new();
    let mut handler = String::new();
    let mut direct = false;
    let mut command = false;
    macro_rules! flush {
        () => {
            if in_method && direct && command && !name.is_empty() && !handler.is_empty() {
                out.push(serde_json::json!({
                    "name": name.clone(), "handler": handler.clone(),
                }));
            }
        };
    }
    for line in text.lines() {
        let l = line.trim();
        if l.starts_with('[') {
            flush!();
            in_method = l == "[[method]]";
            name.clear();
            handler.clear();
            direct = false;
            command = false;
            continue;
        }
        if !in_method {
            continue;
        }
        if name.is_empty() {
            if let Some(v) = val(l, "name") {
                name = v;
                continue;
            }
        }
        if handler.is_empty() {
            if let Some(v) = val(l, "handler") {
                handler = v;
                continue;
            }
        }
        if !direct && l == "direct = true" {
            direct = true;
        }
        if !command && l == "command = true" {
            command = true;
        }
    }
    flush!();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_manifest_picks_panels_with_open() {
        // 面板级入口：只有带 open 的 [[panel]] 才被收成子入口；顶层 id/name 照常
        let text = r#"
id = "zimeiti"
name = "自媒体"
capabilities = ["db", "llm"]

[[panel]]
type = "schema"
name = "board"
label = "选题看板"
src = "panel/board.schema.json"

[[panel]]
type = "schema"
name = "materials"
label = "素材库"
src = "panel/materials.schema.json"
open = "mat_list"

[[tool]]
id = "add"
"#;
        let (id, name, panels) = parse_manifest(text).expect("应解析出插件");
        assert_eq!(id, "zimeiti");
        assert_eq!(name, "自媒体");
        assert_eq!(panels.len(), 1);
        assert_eq!(panels[0]["name"], "materials");
        assert_eq!(panels[0]["label"], "素材库");
        assert_eq!(panels[0]["open"], "mat_list");
    }

    #[test]
    fn parse_manifest_without_panels_or_open() {
        // 无 [[panel]] / panel 无 open → panels 为空；缺 id/name → None
        let (id, name, panels) = parse_manifest("id = \"notes\"\nname = \"笔记\"\n").unwrap();
        assert_eq!((id.as_str(), name.as_str()), ("notes", "笔记"));
        assert!(panels.is_empty());
        let no_open = "id = \"z\"\nname = \"Z\"\n\n[[panel]]\nname = \"board\"\nlabel = \"看板\"\n";
        assert!(parse_manifest(no_open).unwrap().2.is_empty());
        assert!(parse_manifest("name = \"孤儿\"\n").is_none()); // 缺 id
    }

    #[test]
    fn parse_manifest_strips_trailing_comment_after_value() {
        // 行尾注释：open = "mat_list"   # 说明 —— 值只能取到第一个闭引号，注释不许混进方法名
        let text = "id = \"zimeiti\"\nname = \"自媒体\"\n\n[[panel]]\nname = \"materials\"\nlabel = \"素材库\"\nopen = \"mat_list\"   # 面板级入口\n";
        let (_, _, panels) = parse_manifest(text).expect("应解析出插件");
        assert_eq!(panels[0]["open"], "mat_list");
        assert_eq!(panels[0]["label"], "素材库");
    }

    #[test]
    fn parse_api_commands_picks_direct_and_command_only() {
        // 只有 direct=true 且 command=true 的方法才收；缺任一标记/参数方法不收
        let text = r#"
[[method]]
name = "json_format"
handler = "toolbox.json_format"
direct = true
command = true

[[method]]
name = "list"
handler = "toolbox.open"
direct = true
panel = "toolbox:main"

[[method]]
name = "history"
handler = "coding.history"
direct = true
quiet = true
"#;
        let cmds = parse_api_commands(text);
        assert_eq!(cmds.len(), 1);
        assert_eq!(cmds[0]["name"], "json_format");
        assert_eq!(cmds[0]["handler"], "toolbox.json_format");
    }
}
