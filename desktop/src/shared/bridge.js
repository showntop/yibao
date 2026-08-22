// 译宝插件面板桥 SDK(单一事实源)。
// 两条注入路径共用本文件:旧 srcdoc 面板由 WebviewPanel 内联注入(?raw import);
// module 面板由 Rust yibao-plugin:// 协议层 serve /__yibao__/bridge.js(include_bytes!)。
// 必须出现在插件自有脚本之前(协议层注入到 <head> 之后),否则插件脚本执行时 window.yibao 未定义。
// 警告:本文件会被内联进 srcdoc 的 <script> 标签——内容里禁止出现字面 "</script>"(会截断注入脚本)。
(function () {
  window.YIBAO_BRIDGE_VERSION = 1;
  var seq = 0;
  var pending = new Map();
  var initCbs = [];
  var msgCbs = [];
  window.yibao = {
    invoke: function (method, params) {
      return new Promise(function (resolve, reject) {
        var id = ++seq;
        pending.set(id, { resolve: resolve, reject: reject });
        parent.postMessage({ src: "yibao-webview", id: id, method: method, params: params || {} }, "*");
      });
    },
    onInit: function (cb) { initCbs.push(cb); },
    // 事件上报(iframe → 父,无 id 无回包):父侧 emit("panel-event", name, payload)
    emitEvent: function (name, payload) {
      parent.postMessage({ src: "yibao-webview", event: name, payload: payload }, "*");
    },
    // 收 host 任意消息(init 与 invoke 响应之外的,如 {type:"ping"})
    onMessage: function (cb) { msgCbs.push(cb); }
  };
  window.addEventListener("message", function (ev) {
    var d = ev.data;
    if (!d || d.src !== "yibao-host") return;
    if (d.type === "init") {
      initCbs.forEach(function (cb) { try { cb(d.data, d); } catch (e) { console.error(e); } });
      return;
    }
    var p = pending.get(d.id);
    if (p) {
      pending.delete(d.id);
      if (d.ok) p.resolve(d.result);
      else p.reject(new Error(d.error || "调用失败"));
      return;
    }
    msgCbs.forEach(function (cb) { try { cb(d); } catch (e) { console.error(e); } });
  });
})();
