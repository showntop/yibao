/* 娱乐面板逻辑（module 面板）：桥 SDK 由 yibao-plugin:// 协议层注入（window.yibao.invoke/onInit）。
 * 数据获取全部走桥（CSP connect-src 'none'）；视频/音乐内嵌官方播放器 iframe 播放。 */
(function () {
  function $(id) { return document.getElementById(id); }
  function show(el) { el.classList.remove("hidden"); }
  function hide(el) { el.classList.add("hidden"); }

  var toastTimer = null;
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 1400);
  }

  function openUrl(url) {
    window.yibao.invoke("native:open_url", { url: url }).then(function () {
      toast("已用浏览器打开");
    }).catch(function (e) {
      toast(e && e.message ? e.message : "打开失败");
    });
  }

  // ---- 页签 ----
  var tab = "videos";
  function showTab(t) {
    tab = t;
    $("tab-videos").className = t === "videos" ? "on" : "";
    $("tab-music").className = t === "music" ? "on" : "";
    $("tab-quote").className = t === "quote" ? "on" : "";
    $("page-videos").className = t === "videos" ? "" : "hidden";
    $("page-music").className = t === "music" ? "" : "hidden";
    $("page-quote").className = t === "quote" ? "" : "hidden";
    if (t === "videos" && !vidLoaded) loadVideos();
    if (t === "music" && !muInit) initMusic();
    if (t === "quote" && !qtLoaded) loadQuotes();
  }
  $("tab-videos").addEventListener("click", function () { showTab("videos"); });
  $("tab-music").addEventListener("click", function () { showTab("music"); });
  $("tab-quote").addEventListener("click", function () { showTab("quote"); });

  // ---- 视频热榜 + 站内搜索 ----
  var REGIONS = [
    ["0", "全站"], ["1", "动画"], ["3", "音乐"], ["4", "游戏"], ["5", "影视"],
    ["13", "番剧"], ["36", "科技"], ["119", "鬼畜"], ["129", "舞蹈"],
    ["155", "娱乐"], ["160", "生活"], ["188", "知识"],
  ];
  var vidLoaded = false;
  var curTid = "0";
  var curPlayer = null; // 当前播放的视频 {title, url, bvid}
  var curKw = ""; // 当前搜索关键词

  // 站内搜索：切到搜索视图，内嵌 B站官方搜索页（点结果即在站内视频页播放）
  function showSearchView(kw, url) {
    curKw = kw;
    $("search-title").textContent = "搜索「" + kw + "」";
    $("search-frame").src = url;
    hide($("vid-main"));
    hide($("player-view"));
    show($("search-view"));
  }
  function backFromSearch() {
    $("search-frame").src = "";
    hide($("search-view"));
    show($("vid-main"));
  }
  $("search-back").addEventListener("click", backFromSearch);
  $("search-open").addEventListener("click", function () {
    if (curKw) {
      openUrl("https://search.bilibili.com/all?keyword=" + encodeURIComponent(curKw));
    }
  });
  function searchVideos() {
    var kw = $("vid-kw").value.trim();
    if (!kw) { toast("先输入想看的内容"); return; }
    var btn = $("vid-search");
    btn.disabled = true;
    btn.textContent = "搜索中…";
    window.yibao.invoke("fun.videos", { keyword: kw }).then(function (r) {
      if (r && r.search_url) {
        showSearchView(r.keyword || kw, r.search_url);
      } else {
        toast("搜索失败，稍后重试");
      }
    }).catch(function (e) {
      toast(e && e.message ? e.message : "搜索失败");
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = "搜索";
    });
  }
  $("vid-search").addEventListener("click", searchVideos);
  $("vid-kw").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); searchVideos(); }
  });

  function fillSelect(sel, pairs, cur) {
    sel.innerHTML = "";
    pairs.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p[0];
      o.textContent = p[1];
      if (String(p[0]) === String(cur)) o.selected = true;
      sel.appendChild(o);
    });
  }
  fillSelect($("vid-tid"), REGIONS, "0");

  function renderVideos(rows) {
    var list = $("vid-list");
    list.innerHTML = "";
    if (!rows || !rows.length) {
      var e = document.createElement("div");
      e.className = "empty";
      e.innerHTML = '<div class="t">这一批没有拿到内容</div><div class="s">点「换一批」再试试</div>';
      list.appendChild(e);
      return;
    }
    rows.forEach(function (v, i) {
      var it = document.createElement("div");
      it.className = "item";
      var thumb = document.createElement("img");
      thumb.className = "thumb";
      thumb.alt = "";
      if (v.pic) { thumb.src = v.pic; } else { thumb.style.display = "none"; }
      var idx = document.createElement("span");
      idx.className = "idx" + (i < 3 ? " top3" : "");
      idx.textContent = String(i + 1);
      var body = document.createElement("div");
      body.className = "body";
      var t = document.createElement("div");
      t.className = "t";
      t.textContent = v.title || "";
      var m = document.createElement("div");
      m.className = "m";
      var parts = [];
      if (v.author) parts.push(v.author);
      if (v.views) parts.push(v.views + " 播放");
      if (v.duration) parts.push(v.duration);
      if (v.region) parts.push(v.region);
      m.textContent = parts.join(" · ");
      body.appendChild(t);
      body.appendChild(m);
      var go = document.createElement("span");
      go.className = "go";
      go.textContent = "›";
      it.appendChild(thumb);
      it.appendChild(idx);
      it.appendChild(body);
      it.appendChild(go);
      it.addEventListener("click", function () { playVideo(v); });
      list.appendChild(it);
    });
  }

  // 面板内嵌 B站官方播放器（免登录）；失败可退回浏览器
  function playVideo(v) {
    if (!v || !v.bvid) return;
    curPlayer = v;
    $("player-title").textContent = v.title || "";
    $("player-frame").src =
      "https://player.bilibili.com/player.html?bvid=" + encodeURIComponent(v.bvid) +
      "&page=1&high_quality=1&danmaku=1&autoplay=1";
    hide($("vid-main"));
    show($("player-view"));
  }
  $("player-back").addEventListener("click", function () {
    $("player-frame").src = "";
    hide($("player-view"));
    show($("vid-main"));
  });
  $("player-open").addEventListener("click", function () {
    if (curPlayer && curPlayer.url) openUrl(curPlayer.url);
  });

  function loadVideos() {
    hide($("vid-err"));
    $("vid-list").innerHTML = '<div class="empty"><div class="t">加载中…</div></div>';
    var btn = $("vid-refresh");
    var old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "加载中…";
    window.yibao.invoke("fun.videos", { tid: curTid, limit: 12 }).then(function (r) {
      vidLoaded = true;
      renderVideos(r && r.rows ? r.rows : []);
    }).catch(function (e) {
      $("vid-err").textContent = e && e.message ? e.message : "视频拉取失败，稍后重试";
      show($("vid-err"));
      renderVideos([]);
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = old;
    });
  }
  $("vid-refresh").addEventListener("click", loadVideos);
  $("vid-tid").addEventListener("change", function () {
    curTid = $("vid-tid").value;
    loadVideos();
  });

  // ---- 音乐直达 ----
  var muInit = false;
  function renderMusicItems(container, items) {
    container.innerHTML = "";
    (items || []).forEach(function (it) {
      var row = document.createElement("div");
      row.className = "item";
      var body = document.createElement("div");
      body.className = "body";
      var t = document.createElement("div");
      t.className = "t";
      t.textContent = it.name || "";
      var m = document.createElement("div");
      m.className = "m";
      m.textContent = it.note || (it.url || "").replace(/^https?:\/\//, "");
      body.appendChild(t);
      body.appendChild(m);
      row.appendChild(body);
      row.addEventListener("click", function () { openUrl(it.url); });
      container.appendChild(row);
    });
  }

  // 网易云歌曲 → 面板内嵌官方播放器（outchain）
  function playSong(s) {
    if (!s || !s.embed_url) return;
    $("song-frame").src = s.embed_url;
    show($("song-embed"));
    toast("已载入播放器");
  }

  // 热歌榜列表（排名/歌名/歌手），点歌即内嵌播放
  function renderChart(items) {
    var list = $("mu-chart");
    list.innerHTML = "";
    if (!items || !items.length) {
      list.innerHTML = '<div class="empty"><div class="t">热歌榜没拉到</div><div class="s">用「搜歌」试试</div></div>';
      return;
    }
    items.forEach(function (s, i) {
      var row = document.createElement("div");
      row.className = "item";
      var idx = document.createElement("span");
      idx.className = "idx" + (i < 3 ? " top3" : "");
      idx.textContent = String(i + 1);
      var body = document.createElement("div");
      body.className = "body";
      var t = document.createElement("div");
      t.className = "t";
      t.textContent = s.name || "";
      var m = document.createElement("div");
      m.className = "m";
      var parts = [];
      if (s.artist) parts.push(s.artist);
      if (s.duration) parts.push(s.duration);
      m.textContent = parts.join(" · ");
      body.appendChild(t);
      body.appendChild(m);
      var play = document.createElement("button");
      play.className = "play";
      play.textContent = "播放";
      play.addEventListener("click", function (ev) {
        ev.stopPropagation();
        playSong(s);
      });
      row.appendChild(idx);
      row.appendChild(body);
      row.appendChild(play);
      row.addEventListener("click", function () { playSong(s); });
      list.appendChild(row);
    });
  }

  // QQ 音乐搜索视图：版权在 QQ 的艺人（如周杰伦）原版——内嵌 QQ 搜索页，点第一个即播
  var curQqKw = "";
  function showQqSearch(kw, url) {
    curQqKw = kw;
    $("qq-title").textContent = "QQ 音乐「" + kw + "」";
    $("qq-frame").src = url;
    hide($("mu-result-card"));
    hide($("song-embed"));
    show($("qq-search-view"));
  }
  function backFromQqSearch() {
    $("qq-frame").src = "";
    hide($("qq-search-view"));
    if ($("mu-result").children.length) show($("mu-result-card")); // 从搜索进来的才回结果卡
  }
  $("qq-back").addEventListener("click", backFromQqSearch);
  $("qq-open").addEventListener("click", function () {
    if (curQqKw) openUrl("https://y.qq.com/n/ryqq/search?w=" + encodeURIComponent(curQqKw));
  });
  $("mu-qq-link").addEventListener("click", function () {
    var kw = $("mu-result-kw").textContent.trim();
    if (!kw) { toast("先搜索一首歌"); return; }
    showQqSearch(kw, "https://y.qq.com/n/ryqq/search?w=" + encodeURIComponent(kw));
  });

  function renderSongs(songs) {
    var list = $("mu-result");
    list.innerHTML = "";
    (songs || []).forEach(function (s) {
      var row = document.createElement("div");
      row.className = "item";
      var idx = document.createElement("span");
      idx.className = "idx";
      idx.textContent = "♪";
      var body = document.createElement("div");
      body.className = "body";
      var t = document.createElement("div");
      t.className = "t";
      t.textContent = s.name || "";
      var m = document.createElement("div");
      m.className = "m";
      var parts = [];
      if (s.artist) parts.push(s.artist);
      if (s.album) parts.push(s.album);
      if (s.duration) parts.push(s.duration);
      m.textContent = parts.join(" · ");
      body.appendChild(t);
      body.appendChild(m);
      var play = document.createElement("button");
      play.className = "play";
      play.textContent = "播放";
      play.addEventListener("click", function (ev) {
        ev.stopPropagation();
        playSong(s);
      });
      row.appendChild(idx);
      row.appendChild(body);
      row.appendChild(play);
      row.addEventListener("click", function () { playSong(s); });
      list.appendChild(row);
    });
  }

  function initMusic() {
    muInit = true;
    window.yibao.invoke("fun.music", {}).then(function (r) {
      renderChart(r && r.chart ? r.chart : []);
      renderMusicItems($("mu-platforms"), r && r.platforms ? r.platforms : []);
      var hot = $("mu-hot");
      hot.innerHTML = "";
      ((r && r.keywords) || []).forEach(function (kw) {
        var c = document.createElement("button");
        c.className = "chip";
        c.textContent = kw;
        c.addEventListener("click", function () {
          $("mu-kw").value = kw;
          searchMusic();
        });
        hot.appendChild(c);
      });
    }).catch(function () {
      renderChart([]);
      renderMusicItems($("mu-platforms"), []);
    });
  }

  function searchMusic() {
    var kw = $("mu-kw").value.trim();
    if (!kw) { toast("先输入想听的歌或歌手"); return; }
    var btn = $("mu-search");
    btn.disabled = true;
    btn.textContent = "搜索中…";
    hide($("song-embed"));
    window.yibao.invoke("fun.music", { kw: kw }).then(function (r) {
      $("mu-result-kw").textContent = kw;
      if (r && r.songs && r.songs.length) {
        renderSongs(r.songs);
        show($("mu-result-card"));
        toast("点歌直接听");
      } else {
        // 网易云搜索不可用 → 退回平台搜索链接
        renderSongs([]);
        $("mu-result-kw").textContent = kw + "（网易云暂不可用，用平台搜索）";
        renderMusicItems($("mu-result"), r && r.search ? r.search : []);
        show($("mu-result-card"));
      }
    }).catch(function (e) {
      hide($("mu-result-card"));
      toast(e && e.message ? e.message : "搜索失败");
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = "搜索";
    });
  }
  $("mu-search").addEventListener("click", searchMusic);
  $("mu-kw").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); searchMusic(); }
  });

  // ---- 每日一言 ----
  var QT_CATS = [
    ["", "随机"], ["d", "文学"], ["h", "影视"], ["i", "诗词"], ["a", "动画"],
    ["b", "漫画"], ["c", "游戏"], ["j", "网易云"], ["k", "哲学"], ["l", "抖机灵"], ["e", "原创"],
  ];
  var quotes = [];
  var qi = -1;
  var qtLoaded = false;
  fillSelect($("qt-cat"), QT_CATS, "");

  function renderQuote() {
    var q = quotes[qi];
    if (!q) {
      $("qt-text").textContent = "加载中…";
      $("qt-text").className = "quote-text quote-loading";
      $("qt-from").textContent = "";
      return;
    }
    $("qt-text").className = "quote-text";
    $("qt-text").textContent = q.text || "";
    $("qt-from").textContent = q.from || "佚名";
  }

  function loadQuotes() {
    hide($("qt-err"));
    var cat = $("qt-cat").value;
    renderQuote();
    window.yibao.invoke("fun.quote", { cat: cat, count: 5 }).then(function (r) {
      qtLoaded = true;
      quotes = r && r.rows ? r.rows : [];
      qi = quotes.length ? 0 : -1;
      if (!quotes.length) {
        $("qt-text").className = "quote-text quote-loading";
        $("qt-text").textContent = "这一批没有拿到内容，点「换一句」再试试";
        $("qt-from").textContent = "";
        return;
      }
      renderQuote();
    }).catch(function (e) {
      $("qt-err").textContent = e && e.message ? e.message : "一言拉取失败，稍后重试";
      show($("qt-err"));
      $("qt-text").className = "quote-text quote-loading";
      $("qt-text").textContent = "—";
      $("qt-from").textContent = "";
    });
  }

  function nextQuote() {
    if (qi + 1 < quotes.length) {
      qi += 1;
      renderQuote();
      return;
    }
    loadQuotes();
  }
  $("qt-next").addEventListener("click", nextQuote);
  $("qt-cat").addEventListener("change", function () { quotes = []; qi = -1; loadQuotes(); });

  // AI 讲段子：LLM 生成，失败后端自动降级 hitokoto 抖机灵
  $("qt-joke").addEventListener("click", function () {
    var btn = $("qt-joke");
    var old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "讲一个…";
    window.yibao.invoke("fun.joke", {}).then(function (r) {
      if (r && r.text) {
        quotes = [];
        qi = -1;
        $("qt-text").className = "quote-text";
        $("qt-text").textContent = r.text;
        $("qt-from").textContent = (r.from || "AI 段子手") + (r.via === "llm" ? "（AI 生成）" : "");
      } else {
        toast("没讲出来，再试试");
      }
    }).catch(function (e) {
      toast(e && e.message ? e.message : "AI 没讲出来");
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = old;
    });
  });

  // ---- 面板事件：对话流打开时定位 ----
  // 宿主主题通道（WebviewPanel 推 {type:"theme"}）：显式值写 data-theme 命中显式深色块；
  // 未收到则跟随系统（媒体查询块）。
  if (window.yibao && window.yibao.onMessage) {
    window.yibao.onMessage(function (m) {
      if (!m || m.type !== "theme") return;
      if (m.theme === "light" || m.theme === "dark") document.documentElement.dataset.theme = m.theme;
      else delete document.documentElement.dataset.theme;
    });
  }
  window.yibao.onInit(function (data) {
    if (!data) return;
    // QQ 音乐原版：LLM 调 fun.music {kw, source:"qq"} → data 带 mode=qq + qq_search_url → 内嵌 QQ 搜索页
    if (data.mode === "qq" && data.qq_search_url) {
      showTab("music");
      showQqSearch(data.kw || "", data.qq_search_url);
      return;
    }
    // 音乐直达：LLM 调 fun.music {kw} 后，data 带 kw + songs → 切音乐 tab + 渲染结果 + 自动播第一首
    if (data.kw) {
      showTab("music");
      if (data.songs && data.songs.length) {
        renderSongs(data.songs);
        $("mu-result-kw").textContent = data.kw;
        show($("mu-result-card"));
        playSong(data.songs[0]); // 点开即播
      } else if (data.songs_failed) {
        // 网易云挂了 → 退回平台搜索链接
        renderSongs([]);
        $("mu-result-kw").textContent = data.kw + "（网易云暂不可用，用平台搜索）";
        renderMusicItems($("mu-result"), data.search || []);
        show($("mu-result-card"));
      }
      return;
    }
    // 精准搜索：LLM 调 fun.videos {keyword} 后，面板事件 data 带 keyword/search_url → 自动切搜索视图
    if (data.keyword) {
      showTab("videos");
      if (data.search_url) showSearchView(data.keyword, data.search_url);
      return;
    }
    if (!data.tab) return;
    if (data.tab === "music") showTab("music");
    else if (data.tab === "quote") showTab("quote");
    else showTab("videos");
  });
})();
