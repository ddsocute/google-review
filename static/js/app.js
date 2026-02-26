/* ============================================================
   Google Maps Restaurant Review AI Analyzer - Frontend Logic
   ============================================================ */

(function () {
    "use strict";

    // ---------------------------------------------------------------------------
    // DOM refs
    // ---------------------------------------------------------------------------
    const urlInput = document.getElementById("urlInput");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const loadingSection = document.getElementById("loadingSection");
    const skeletonSection = document.getElementById("skeletonSection");
    const errorSection = document.getElementById("errorSection");
    const errorMessage = document.getElementById("errorMessage");
    const resultsSection = document.getElementById("resultsSection");

    const step1 = document.getElementById("step1");
    const step2 = document.getElementById("step2");
    const step3 = document.getElementById("step3");
    const progressFill = document.getElementById("loadingProgressFill");
    const progressText = document.getElementById("loadingProgressText");

    var lastAnalyzedUrl = "";
    var lastAnalysisData = null;
    var radarChartInstance = null;
    var trendChartInstance = null;

    // Input mode: 'url' (default) or 'name'
    var inputMode = "url";
    var inputModeButtons = document.querySelectorAll(".input-mode-btn");
    var inputHint = document.getElementById("inputHint");

    // ---------------------------------------------------------------------------
    // Search History (localStorage)
    // ---------------------------------------------------------------------------
    var HISTORY_KEY = "analysisHistory";
    var MAX_HISTORY = 8;

    function getHistory() {
        try {
            return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
        } catch (e) { return []; }
    }

    function saveToHistory(name, url) {
        var history = getHistory().filter(function (h) { return h.url !== url; });
        history.unshift({ name: name, url: url, time: Date.now() });
        if (history.length > MAX_HISTORY) history = history.slice(0, MAX_HISTORY);
        localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
        renderHistory();
    }

    function renderHistory() {
        var section = document.getElementById("historySection");
        var container = document.getElementById("historyTags");
        var history = getHistory();
        if (!section || !container) return;
        if (history.length === 0) {
            section.style.display = "none";
            return;
        }
        container.innerHTML = "";
        history.forEach(function (h) {
            var tag = document.createElement("button");
            tag.className = "history-tag";
            tag.textContent = h.name;
            tag.title = h.url;
            tag.addEventListener("click", function () {
                urlInput.value = h.url;
                startAnalysis();
            });
            container.appendChild(tag);
        });
        // Add clear button
        var clearBtn = document.createElement("button");
        clearBtn.className = "history-tag history-clear";
        clearBtn.textContent = "清除紀錄";
        clearBtn.addEventListener("click", function () {
            localStorage.removeItem(HISTORY_KEY);
            renderHistory();
        });
        container.appendChild(clearBtn);
    }
    renderHistory();

    // History dropdown behavior: only show when input is focused, like Google search
    function showHistoryDropdown() {
        var section = document.getElementById("historySection");
        if (!section) return;
        var history = getHistory();
        if (!history.length) {
            section.style.display = "none";
            return;
        }
        section.style.display = "block";
    }

    function hideHistoryDropdown() {
        var section = document.getElementById("historySection");
        if (!section) return;
        section.style.display = "none";
    }

    // ---------------------------------------------------------------------------
    // Model toggle
    // ---------------------------------------------------------------------------
    var selectedModel = "gemini-3-flash-preview";
    document.querySelectorAll(".model-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll(".model-btn").forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            selectedModel = btn.getAttribute("data-model");
        });
    });

    // ---------------------------------------------------------------------------
    // URL validation (client-side)
    // ---------------------------------------------------------------------------
    const URL_PATTERNS = [
        // 支援：餐廳頁面、Google Maps 短網址與搜尋頁面
        /https?:\/\/(www\.)?google\.(com|com\.\w{2})\/maps\/place\//i,
        /https?:\/\/maps\.app\.goo\.gl\//i,
        /https?:\/\/(www\.)?google\.(com|com\.\w{2})\/maps\/search\//i,
    ];

    function isValidUrl(url) {
        return URL_PATTERNS.some(function (p) { return p.test(url); });
    }

    // ---------------------------------------------------------------------------
    // UI helpers
    // ---------------------------------------------------------------------------
    function show(el) { if (el) el.classList.remove("hidden"); }
    function hide(el) { if (el) el.classList.add("hidden"); }

    function setStep(activeStep) {
        [step1, step2, step3].forEach(function (s, i) {
            s.classList.remove("active", "done");
            if (i + 1 < activeStep) s.classList.add("done");
            if (i + 1 === activeStep) s.classList.add("active");
        });
    }

    function setProgress(percent, text) {
        if (progressFill) progressFill.style.width = percent + "%";
        if (progressText && text) progressText.textContent = text;
    }

    window.resetUI = function () {
        hide(loadingSection);
        hide(skeletonSection);
        hide(errorSection);
        hide(resultsSection);
        analyzeBtn.classList.remove("loading");
        analyzeBtn.disabled = false;
        urlInput.value = "";
        urlInput.focus();
        hideHistoryDropdown();
    };

    window.retryAnalysis = function () {
        hide(errorSection);
        if (lastAnalyzedUrl) {
            urlInput.value = lastAnalyzedUrl;
            startAnalysis();
        } else {
            window.resetUI();
        }
    };

    function showError(msg) {
        hide(loadingSection);
        hide(skeletonSection);
        hide(resultsSection);
        errorMessage.textContent = msg;

        var title = document.getElementById("errorTitle");
        if (msg.includes("逾時")) {
            title.textContent = "分析逾時";
        } else if (msg.includes("額度")) {
            title.textContent = "額度不足";
        } else if (msg.includes("找到") || msg.includes("沒有")) {
            title.textContent = "找不到評論";
        } else {
            title.textContent = "分析失敗";
        }

        show(errorSection);
        analyzeBtn.classList.remove("loading");
        analyzeBtn.disabled = false;
    }

    // ---------------------------------------------------------------------------
    // Animated counter
    // ---------------------------------------------------------------------------
    function animateValue(el, start, end, duration, suffix) {
        suffix = suffix || "";
        var range = end - start;
        var startTime = null;
        function tick(ts) {
            if (!startTime) startTime = ts;
            var progress = Math.min((ts - startTime) / duration, 1);
            var eased = 1 - Math.pow(1 - progress, 3);
            var current = start + range * eased;
            el.textContent = (Number.isInteger(end) ? Math.round(current) : current.toFixed(1)) + suffix;
            if (progress < 1) requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    // ---------------------------------------------------------------------------
    // Render: Restaurant Intro (Section 1)
    // ---------------------------------------------------------------------------
    function renderIntro(data) {
        document.getElementById("restaurantName").textContent = data.restaurant_name || "餐廳分析結果";
        document.getElementById("reviewCount").textContent =
            "已分析 " + (data.total_reviews_analyzed || 0) + " 則評論";
        document.getElementById("restaurantIntro").textContent =
            data.restaurant_intro || data.dining_tips || "暫無餐廳介紹資訊。";
    }

    // ---------------------------------------------------------------------------
    // Render: Radar Chart
    // ---------------------------------------------------------------------------
    function renderRadarChart(data) {
        var canvas = document.getElementById("radarChart");
        var card = document.getElementById("radarCard");
        if (!canvas || typeof Chart === "undefined") {
            if (card) card.style.display = "none";
            return;
        }

        var scores = [
            data.taste ? data.taste.score : 0,
            data.service ? data.service.score : 0,
            data.environment ? data.environment.score : 0,
            data.value_for_money ? data.value_for_money.score : 0,
        ];

        if (radarChartInstance) {
            radarChartInstance.destroy();
            radarChartInstance = null;
        }

        var isDark = document.documentElement.getAttribute("data-theme") === "dark";

        card.style.display = "block";
        radarChartInstance = new Chart(canvas, {
            type: "radar",
            data: {
                labels: ["口味", "服務", "環境", "CP值"],
                datasets: [{
                    label: "評分",
                    data: scores,
                    backgroundColor: "rgba(26,115,232,0.15)",
                    borderColor: "#1a73e8",
                    borderWidth: 2,
                    pointBackgroundColor: "#1a73e8",
                    pointBorderColor: isDark ? "#303134" : "#fff",
                    pointBorderWidth: 2,
                    pointRadius: 5,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: { legend: { display: false } },
                scales: {
                    r: {
                        min: 0,
                        max: 10,
                        ticks: {
                            stepSize: 2,
                            font: { size: 11 },
                            backdropColor: "transparent",
                            color: isDark ? "#9aa0a6" : undefined,
                        },
                        pointLabels: {
                            font: { size: 14, weight: "bold", family: "'Noto Sans TC', sans-serif" },
                            color: isDark ? "#e8eaed" : "#202124",
                        },
                        grid: { color: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)" },
                        angleLines: { color: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)" },
                    }
                }
            }
        });
    }

    // ---------------------------------------------------------------------------
    // Render: Dimensions only (no gauge) (Section 2)
    // ---------------------------------------------------------------------------
    function renderOverviewAndDimensions(data) {
        var pairs = [
            ["tasteScore", data.taste ? data.taste.score : 0],
            ["serviceScore", data.service ? data.service.score : 0],
            ["envScore", data.environment ? data.environment.score : 0],
            ["cpScore", data.value_for_money ? data.value_for_money.score : 0],
        ];
        pairs.forEach(function (p) {
            animateValue(document.getElementById(p[0]), 0, p[1], 1200);
        });
        renderDimensionTabs(data);
    }

    function renderDimensionTabs(data) {
        if (data.taste) {
            document.getElementById("tasteSummary").textContent = data.taste.summary || "";
            animateProgress("tasteProgress", data.taste.score);
            animateValue(document.getElementById("tasteVal"), 0, data.taste.score, 1000);
            renderTags("tastePositive", data.taste.positive_keywords, true);
            renderTags("tasteNegative", data.taste.negative_keywords, false);
        }
        if (data.service) {
            document.getElementById("serviceSummary").textContent = data.service.summary || "";
            animateProgress("serviceProgress", data.service.score);
            animateValue(document.getElementById("serviceVal"), 0, data.service.score, 1000);
            renderTags("servicePositive", data.service.positive_keywords, true);
            renderTags("serviceNegative", data.service.negative_keywords, false);
        }
        if (data.environment) {
            document.getElementById("envSummary").textContent = data.environment.summary || "";
            animateProgress("envProgress", data.environment.score);
            animateValue(document.getElementById("envVal"), 0, data.environment.score, 1000);
            renderTags("envPositive", data.environment.positive_keywords, true);
            renderTags("envNegative", data.environment.negative_keywords, false);
        }
        if (data.value_for_money) {
            document.getElementById("valueSummary").textContent = data.value_for_money.summary || "";
            animateProgress("valueProgress", data.value_for_money.score);
            animateValue(document.getElementById("valueVal"), 0, data.value_for_money.score, 1000);
            var pr = document.getElementById("priceRange");
            if (data.value_for_money.price_range) {
                pr.textContent = "💵 " + data.value_for_money.price_range;
                pr.style.display = "inline-block";
            } else {
                pr.style.display = "none";
            }
        }
    }

    function renderTags(elementId, keywords, isPositive) {
        var el = document.getElementById(elementId);
        if (!el) return;
        el.innerHTML = "";
        (keywords || []).forEach(function (kw) {
            var t = document.createElement("span");
            t.className = isPositive ? "tag-positive" : "tag-negative";
            t.textContent = kw;
            el.appendChild(t);
        });
    }

    function animateProgress(id, score) {
        var el = document.getElementById(id);
        setTimeout(function () { el.style.width = (score * 10) + "%"; }, 200);
    }

    // ---------------------------------------------------------------------------
    // Render: Fake Review Warning (Section 3)
    // ---------------------------------------------------------------------------
    function renderFakeWarning(detection) {
        if (!detection) return;
        var pct = detection.percentage || 0;
        // 使用者要求：灌水比例低於 10% 就不要顯示提醒
        if (pct < 10 || (pct <= 0 && detection.suspected_count <= 0)) {
            hide(document.getElementById("fakeReviewSection"));
            return;
        }

        var section = document.getElementById("fakeReviewSection");
        var card = document.getElementById("fakeCard");
        show(section);

        if (pct > 15) {
            card.className = "fake-card level-high";
        } else {
            card.className = "fake-card level-low";
        }

        document.getElementById("fakeBadge").textContent = detection.warning_level || "整體正常";
        document.getElementById("fakeCount").textContent = detection.suspected_count || 0;
        document.getElementById("fakePercent").textContent = (detection.percentage || 0) + "%";
        document.getElementById("fakeTotal").textContent = detection.total_reviews || 0;

        var reasonsEl = document.getElementById("fakeReasons");
        reasonsEl.innerHTML = "";
        (detection.reasons || []).forEach(function (r) {
            var tag = document.createElement("span");
            tag.className = "fake-reason-tag";
            tag.textContent = r;
            reasonsEl.appendChild(tag);
        });

        document.getElementById("fakeDetails").textContent = detection.details || "";

        var timelineEl = document.getElementById("fakeTimeline");
        timelineEl.innerHTML = "";
        var ap = detection.activity_period;
        if (ap) {
            var tl = document.createElement("div");
            tl.className = "timeline-box";
            var tlTitle = document.createElement("div");
            tlTitle.className = "timeline-title";
            tlTitle.textContent = "時間分佈（灌水 / 不自然評論大多出現在什麼時候）";
            tl.appendChild(tlTitle);

            var tlRow = document.createElement("div");
            tlRow.className = "timeline-row";
            var startBlock = document.createElement("div");
            startBlock.className = "timeline-item";
            startBlock.innerHTML =
                '<span class="tl-dot start"></span>' +
                '<span class="tl-label">開始時間</span>' +
                '<span class="tl-date">' + (ap.start_date || "不明") + '</span>';
            tlRow.appendChild(startBlock);

            var endBlock = document.createElement("div");
            endBlock.className = "timeline-item";
            endBlock.innerHTML =
                '<span class="tl-dot ' + (ap.is_ongoing ? "ongoing" : "end") + '"></span>' +
                '<span class="tl-label">' + (ap.is_ongoing ? "目前仍在持續" : "已經結束") + '</span>' +
                '<span class="tl-date">' + (ap.end_date || "不明") + '</span>';
            tlRow.appendChild(endBlock);
            tl.appendChild(tlRow);

            if (ap.description) {
                var desc = document.createElement("p");
                desc.className = "timeline-desc";
                desc.textContent = ap.description;
                tl.appendChild(desc);
            }
            timelineEl.appendChild(tl);
        }
    }

    // ---------------------------------------------------------------------------
    // Render: Dishes
    // ---------------------------------------------------------------------------
    function createDishCard(dish, isGood) {
        var card = document.createElement("div");
        card.className = "dish-card";
        var info = document.createElement("div");
        info.className = "dish-info";
        var nameRow = document.createElement("div");
        nameRow.className = "dish-name-row";
        var nameEl = document.createElement("span");
        nameEl.className = "dish-name";
        nameEl.textContent = dish.name;
        nameRow.appendChild(nameEl);
        if (dish.mentions) {
            var badge = document.createElement("span");
            badge.className = "mention-badge";
            badge.textContent = dish.mentions + "人" + (isGood ? "推薦" : "提及");
            nameRow.appendChild(badge);
        }
        info.appendChild(nameRow);
        var reason = document.createElement("p");
        reason.className = "dish-reason";
        reason.textContent = dish.reason || "";
        info.appendChild(reason);
        if (dish.keywords && dish.keywords.length) {
            var kwDiv = document.createElement("div");
            kwDiv.className = "dish-keywords";
            dish.keywords.forEach(function (kw) {
                var tag = document.createElement("span");
                tag.className = "keyword-tag";
                tag.textContent = kw;
                kwDiv.appendChild(tag);
            });
            info.appendChild(kwDiv);
        }
        card.appendChild(info);
        return card;
    }

    function renderDishes(data) {
        var recContainer = document.getElementById("recommendedDishes");
        var notContainer = document.getElementById("notRecommendedDishes");
        recContainer.innerHTML = "";
        notContainer.innerHTML = "";
        var recDishes = data.recommended_dishes || [];
        var notDishes = data.not_recommended_dishes || [];
        if (recDishes.length === 0) {
            recContainer.innerHTML = '<p class="no-dishes">評論中未明確提到特定推薦菜色</p>';
        } else {
            recDishes.forEach(function (d) { recContainer.appendChild(createDishCard(d, true)); });
        }
        if (notDishes.length === 0) {
            notContainer.innerHTML = '<p class="no-dishes">評論中未明確提到特定不推薦菜色</p>';
        } else {
            notDishes.forEach(function (d) { notContainer.appendChild(createDishCard(d, false)); });
        }
    }

    // ---------------------------------------------------------------------------
    // Render: Food Photo Gallery
    // ---------------------------------------------------------------------------
    function renderFoodPhotos(photos) {
        var section = document.getElementById("foodPhotoSection");
        var gallery = document.getElementById("photoGallery");
        if (!photos || photos.length === 0) { hide(section); return; }
        show(section);
        gallery.innerHTML = "";
        photos.forEach(function (url) {
            var img = document.createElement("img");
            img.className = "gallery-photo";
            img.src = url;
            img.alt = "食物照片";
            img.loading = "lazy";
            img.onerror = function () { img.style.display = "none"; };
            img.onclick = function () { openLightbox(url); };
            gallery.appendChild(img);
        });
    }

    // ---------------------------------------------------------------------------
    // Render: Scene Recommendations (Section 7)
    // ---------------------------------------------------------------------------
    function renderSceneRecommendations(scenes) {
        var section = document.getElementById("sceneSection");
        var grid = document.getElementById("sceneGrid");
        if (!scenes || !scenes.length) { if (section) section.style.display = "none"; return; }
        section.style.display = "block";
        grid.innerHTML = "";
        var icons = { };
        scenes.forEach(function (s) {
            var card = document.createElement("div");
            card.className = "scene-card " + (s.suitable ? "scene-yes" : "scene-no");
            card.innerHTML =
                '<div class="scene-name">' + s.scene + '</div>' +
                '<div class="scene-badge">' + (s.suitable ? "適合" : "不適合") + '</div>' +
                '<div class="scene-desc">' + (s.description || "") + '</div>';
            grid.appendChild(card);
        });
    }

    // ---------------------------------------------------------------------------
    // Render: Best Visit Time (Section 8)
    // ---------------------------------------------------------------------------
    function renderBestVisitTime(visitData) {
        var section = document.getElementById("visitTimeSection");
        var grid = document.getElementById("visitTimeGrid");
        var summary = document.getElementById("visitTimeSummary");
        if (!visitData || !visitData.recommendations) { if (section) section.style.display = "none"; return; }
        section.style.display = "block";
        if (summary) summary.textContent = visitData.summary || "";
        grid.innerHTML = "";
        var crowdColors = { "低": "crowd-low", "中": "crowd-mid", "高": "crowd-high" };
        visitData.recommendations.forEach(function (r) {
            var card = document.createElement("div");
            card.className = "visit-card " + (crowdColors[r.crowding] || "crowd-mid");
            card.innerHTML =
                '<div class="visit-time-label">' + (r.time || "") + '</div>' +
                '<div class="visit-crowd">人潮' + (r.crowding || "中") + '</div>' +
                '<div class="visit-wait">預估等待時間：' + (r.wait_time || "不確定") + '</div>' +
                '<div class="visit-desc">' + (r.description || "") + '</div>';
            grid.appendChild(card);
        });
    }

    // ---------------------------------------------------------------------------
    // Render: Rating Trend Chart (Section 9)
    // ---------------------------------------------------------------------------
    function renderRatingTrend(trend) {
        var section = document.getElementById("trendSection");
        var badge = document.getElementById("trendBadge");
        var summaryEl = document.getElementById("trendSummary");
        var listEl = document.getElementById("trendList");
        var canvas = document.getElementById("trendChart");
        if (!trend || !trend.periods || !trend.periods.length) {
            if (section) section.style.display = "none";
            return;
        }
        section.style.display = "block";
        if (badge) {
            badge.textContent = trend.trend_label || "最近走勢穩定";
            badge.className = "trend-badge trend-" + (trend.trend || "stable");
        }
        if (summaryEl) {
            var sum = trend.summary || "";
            summaryEl.textContent = sum ? "總結來說：" + sum : "";
        }

        // 不再顯示「近1個月 / 3-6個月」這類分段文字描述，只保留圖表與總結
        if (listEl) listEl.innerHTML = "";

        if (!canvas || typeof Chart === "undefined") return;
        if (trendChartInstance) { trendChartInstance.destroy(); trendChartInstance = null; }

        var labels = trend.periods.map(function (p) { return p.period; }).reverse();
        var scores = trend.periods.map(function (p) { return p.avg_score; }).reverse();
        var isDark = document.documentElement.getAttribute("data-theme") === "dark";

        trendChartInstance = new Chart(canvas, {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "平均評分",
                        data: scores,
                        borderColor: "#17120f",
                        backgroundColor: "rgba(23,18,15,0.12)",
                        borderWidth: 3,
                        pointBackgroundColor: "#17120f",
                        pointBorderColor: isDark ? "#15110e" : "#f9f6f0",
                        pointBorderWidth: 2,
                        pointRadius: 5,
                        fill: true,
                        tension: 0.25
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: {
                        ticks: { color: isDark ? "#b1a79a" : "#7a7267" },
                        grid: { color: isDark ? "rgba(244,238,230,0.06)" : "rgba(0,0,0,0.04)" }
                    },
                    y: {
                        min: 1,
                        max: 5,
                        ticks: { stepSize: 0.5, color: isDark ? "#b1a79a" : "#7a7267" },
                        grid: { color: isDark ? "rgba(244,238,230,0.06)" : "rgba(0,0,0,0.04)" }
                    }
                }
            }
        });
    }

    // ---------------------------------------------------------------------------
    // Lightbox
    // ---------------------------------------------------------------------------
    window.openLightbox = function (url) {
        var overlay = document.getElementById("lightboxOverlay");
        var img = document.getElementById("lightboxImg");
        img.src = url;
        overlay.classList.add("active");
        document.body.style.overflow = "hidden";
    };
    window.closeLightbox = function () {
        var overlay = document.getElementById("lightboxOverlay");
        overlay.classList.remove("active");
        document.getElementById("lightboxImg").src = "";
        document.body.style.overflow = "";
    };

    // ---------------------------------------------------------------------------
    // Advanced analysis toggle
    // ---------------------------------------------------------------------------
    window.toggleAdvanced = function () {
        var content = document.getElementById("advancedContent");
        var btn = document.getElementById("advancedToggleBtn");
        if (!content || !btn) return;
        var isHidden = content.classList.contains("hidden");
        if (isHidden) {
            content.classList.remove("hidden");
            btn.classList.add("advanced-toggle-open");
            btn.textContent = "🔍 收合進階分析";
        } else {
            content.classList.add("hidden");
            btn.classList.remove("advanced-toggle-open");
            btn.textContent = "🔍 展開進階分析（評論異常、場合建議、造訪時段）";
        }
    };

    // ---------------------------------------------------------------------------
    // Tabs
    // ---------------------------------------------------------------------------
    document.querySelectorAll(".tab").forEach(function (tab) {
        tab.addEventListener("click", function () {
            document.querySelectorAll(".tab").forEach(function (t) { t.classList.remove("active"); });
            document.querySelectorAll(".tab-content").forEach(function (c) { c.classList.remove("active"); });
            tab.classList.add("active");
            var target = tab.getAttribute("data-tab");
            document.getElementById("tab-" + target).classList.add("active");
        });
    });

    // ---------------------------------------------------------------------------
    // Share: Copy summary text
    // ---------------------------------------------------------------------------
    window.copySummary = function () {
        if (!lastAnalysisData) return;
        var d = lastAnalysisData;
        var text = "食神｜" + (d.restaurant_name || "餐廳") + " - Google Maps 評論分析報告\n\n";
        text += "總評分：" + (d.overall_score || "N/A") + "/10\n";
        text += "口味：" + (d.taste ? d.taste.score : "?") + " | ";
        text += "服務：" + (d.service ? d.service.score : "?") + " | ";
        text += "環境：" + (d.environment ? d.environment.score : "?") + " | ";
        text += "CP值：" + (d.value_for_money ? d.value_for_money.score : "?") + "\n\n";
        if (d.recommended_dishes && d.recommended_dishes.length) {
            text += "推薦菜色：" + d.recommended_dishes.map(function (dd) { return dd.name; }).join("、") + "\n";
        }
        if (d.not_recommended_dishes && d.not_recommended_dishes.length) {
            text += "不推薦菜色：" + d.not_recommended_dishes.map(function (dd) { return dd.name; }).join("、") + "\n";
        }
        if (d.value_for_money && d.value_for_money.price_range) {
            text += "價格區間：" + d.value_for_money.price_range + "\n";
        }
        text += "\n由「食神」整理（Google Maps 餐廳評論洞察報告）";

        navigator.clipboard.writeText(text).then(function () {
            var btn = document.getElementById("copyBtn");
            btn.textContent = "已複製";
            setTimeout(function () { btn.textContent = "複製分析摘要"; }, 2000);
        }).catch(function () {
            // Fallback
            var ta = document.createElement("textarea");
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
            var btn = document.getElementById("copyBtn");
            btn.textContent = "已複製";
            setTimeout(function () { btn.textContent = "複製分析摘要"; }, 2000);
        });
    };

    // ---------------------------------------------------------------------------
    // Share: Social (LINE / Facebook / X)
    // ---------------------------------------------------------------------------
    window.shareTo = function (platform) {
        if (!lastAnalysisData) return;
        var name = lastAnalysisData.restaurant_name || "餐廳";
        var score = lastAnalysisData.overall_score || "?";
        var shareText = "食神｜" + name + " Google Maps 評論分析，整體評分 " + score + "/10";
        var shareUrl = window.location.href;
        var url;
        switch (platform) {
            case "line":
                url = "https://social-plugins.line.me/lineit/share?url=" + encodeURIComponent(shareUrl) + "&text=" + encodeURIComponent(shareText);
                break;
            case "facebook":
                url = "https://www.facebook.com/sharer/sharer.php?u=" + encodeURIComponent(shareUrl) + "&quote=" + encodeURIComponent(shareText);
                break;
            case "x":
                url = "https://twitter.com/intent/tweet?text=" + encodeURIComponent(shareText) + "&url=" + encodeURIComponent(shareUrl);
                break;
        }
        if (url) window.open(url, "_blank", "width=600,height=400");
    };

    // ---------------------------------------------------------------------------
    // Download: PNG
    // ---------------------------------------------------------------------------
    window.downloadReport = function () {
        var btn = document.getElementById("downloadBtn");
        btn.textContent = "產生圖片中...";
        btn.disabled = true;
        if (typeof html2canvas === "undefined") {
            btn.textContent = "下載報告圖片";
            btn.disabled = false;
            alert("圖片產生元件載入失敗，請重新整理頁面後再試");
            return;
        }
        html2canvas(resultsSection, {
            scale: 2, useCORS: true,
            backgroundColor: document.documentElement.getAttribute("data-theme") === "dark" ? "#202124" : "#ffffff",
            logging: false, windowWidth: 860,
        }).then(function (canvas) {
            var link = document.createElement("a");
            var rname = (document.getElementById("restaurantName").textContent || "分析報告").replace(/[\/\\:]/g, "_");
            link.download = rname + "-評論分析報告.png";
            link.href = canvas.toDataURL("image/png");
            link.click();
            btn.textContent = "下載報告圖片";
            btn.disabled = false;
        }).catch(function () {
            btn.textContent = "下載報告圖片";
            btn.textContent = "下載報告圖片";
            btn.disabled = false;
            alert("圖片產生失敗，請重試");
        });
    };

    // ---------------------------------------------------------------------------
    // Download: PDF
    // ---------------------------------------------------------------------------
    window.downloadPDF = function () {
        var btn = document.getElementById("pdfBtn");
        btn.textContent = "產生 PDF 中...";
        btn.disabled = true;
        if (typeof html2canvas === "undefined" || typeof jspdf === "undefined") {
            btn.textContent = "下載 PDF 報告";
            btn.disabled = false;
            alert("PDF 元件載入失敗，請重新整理頁面後再試");
            return;
        }
        html2canvas(resultsSection, {
            scale: 2, useCORS: true,
            backgroundColor: document.documentElement.getAttribute("data-theme") === "dark" ? "#202124" : "#ffffff",
            logging: false, windowWidth: 860,
        }).then(function (canvas) {
            var imgData = canvas.toDataURL("image/jpeg", 0.92);
            var pdf = new jspdf.jsPDF("p", "mm", "a4");
            var pageWidth = pdf.internal.pageSize.getWidth();
            var pageHeight = pdf.internal.pageSize.getHeight();
            var imgWidth = pageWidth - 20;
            var imgHeight = (canvas.height * imgWidth) / canvas.width;
            var y = 10;
            // If image is taller than one page, split across pages
            if (imgHeight <= pageHeight - 20) {
                pdf.addImage(imgData, "JPEG", 10, y, imgWidth, imgHeight);
            } else {
                var remainingHeight = imgHeight;
                var sourceY = 0;
                var pageCanvas = document.createElement("canvas");
                var pageCtx = pageCanvas.getContext("2d");
                while (remainingHeight > 0) {
                    var sliceHeight = Math.min(pageHeight - 20, remainingHeight);
                    var sourceSliceHeight = (sliceHeight / imgHeight) * canvas.height;
                    pageCanvas.width = canvas.width;
                    pageCanvas.height = sourceSliceHeight;
                    pageCtx.drawImage(canvas, 0, sourceY, canvas.width, sourceSliceHeight, 0, 0, canvas.width, sourceSliceHeight);
                    var sliceData = pageCanvas.toDataURL("image/jpeg", 0.92);
                    if (sourceY > 0) pdf.addPage();
                    pdf.addImage(sliceData, "JPEG", 10, 10, imgWidth, sliceHeight);
                    sourceY += sourceSliceHeight;
                    remainingHeight -= sliceHeight;
                }
            }
            var rname = (document.getElementById("restaurantName").textContent || "分析報告").replace(/[\/\\:]/g, "_");
            pdf.save(rname + "-評論分析報告.pdf");
            btn.textContent = "下載 PDF 報告";
            btn.disabled = false;
        }).catch(function () {
            btn.textContent = "下載 PDF 報告";
            btn.textContent = "下載 PDF 報告";
            btn.disabled = false;
            alert("PDF 產生失敗，請重試");
        });
    };

    // ---------------------------------------------------------------------------
    // Render all sections
    // ---------------------------------------------------------------------------
    function renderAllSections(data) {
        renderIntro(data);
        renderRadarChart(data);
        renderOverviewAndDimensions(data);
        renderFakeWarning(data.fake_review_detection);
        renderDishes(data);
        renderFoodPhotos(data.food_photos);
        renderSceneRecommendations(data.scene_recommendations);
        renderBestVisitTime(data.best_visit_time);
        renderRatingTrend(data.rating_trend);
    }

    // ---------------------------------------------------------------------------
    // Main analysis flow
    // ---------------------------------------------------------------------------
    function startAnalysis() {
        var raw = urlInput.value.trim();
        if (!raw) {
            urlInput.focus();
            urlInput.style.borderColor = "#ea4335";
            setTimeout(function () { urlInput.style.borderColor = ""; }, 1500);
            return;
        }

        var url = raw;
        if (inputMode === "name") {
            // 使用店名搜尋：轉成 Google Maps 搜尋網址
            var encoded = encodeURIComponent(raw);
            url = "https://www.google.com/maps/search/" + encoded;
        } else {
            if (!isValidUrl(url)) {
                showError("網址格式不正確, 請貼上 Google Maps 餐廳連結.");
                return;
            }
        }

        lastAnalyzedUrl = url;
        analyzeBtn.classList.add("loading");
        analyzeBtn.disabled = true;
        hide(errorSection);
        hide(resultsSection);
        hide(document.getElementById("fakeReviewSection"));
        hide(document.getElementById("foodPhotoSection"));
        show(loadingSection);
        show(skeletonSection);
        setStep(1);
        setProgress(10, "步驟 1 / 3：正在連接 Google Maps 並抓取評論資料（約 10–20 秒）");

        var stepTimer2 = setTimeout(function () {
            setStep(2);
            setProgress(45, "步驟 2 / 3：AI 正在閱讀評論內容與評分，時間會依評論數量略有不同");
        }, 8000);
        var stepTimer3 = setTimeout(function () {
            setStep(3);
            setProgress(75, "步驟 3 / 3：正在整理圖表與重點摘要，幫你彙整成可閱讀的報告");
        }, 30000);

        var controller = new AbortController();
        var fetchTimeout = setTimeout(function () { controller.abort(); }, 300000);

        fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: url, model: selectedModel }),
            signal: controller.signal,
        })
            .then(function (res) {
                clearTimeout(fetchTimeout);
                clearTimeout(stepTimer2);
                clearTimeout(stepTimer3);
                if (!res.ok) {
                    return res.json().then(function (body) {
                        throw new Error(body.error || "伺服器錯誤 (" + res.status + ")");
                    });
                }
                return res.json();
            })
            .then(function (data) {
                if (data.error) throw new Error(data.error);
                lastAnalysisData = data;
                setProgress(100, "分析完成！");

                // Save to history
                saveToHistory(data.restaurant_name || "未知餐廳", url);

                setTimeout(function () {
                    hide(loadingSection);
                    hide(skeletonSection);
                    show(resultsSection);
                    analyzeBtn.classList.remove("loading");
                    analyzeBtn.disabled = false;
                    renderAllSections(data);
                    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
                }, 400);
            })
            .catch(function (err) {
                clearTimeout(fetchTimeout);
                clearTimeout(stepTimer2);
                clearTimeout(stepTimer3);
                var msg = err.name === "AbortError"
                    ? "分析請求逾時（超過 5 分鐘），請稍後再試或切換到快速模式"
                    : (err.message || "發生未知錯誤, 請稍後再試.");
                showError(msg);
            });
    }

    // ---------------------------------------------------------------------------
    // Event listeners
    // ---------------------------------------------------------------------------
    analyzeBtn.addEventListener("click", startAnalysis);
    urlInput.addEventListener("keydown", function (e) { if (e.key === "Enter") startAnalysis(); });

    // History dropdown show/hide, like Google search suggestions
    urlInput.addEventListener("focus", function () {
        showHistoryDropdown();
    });
    urlInput.addEventListener("blur", function () {
        // 延遲收合，讓點擊歷史紀錄按鈕有時間觸發
        setTimeout(hideHistoryDropdown, 180);
    });

    // Input mode toggle (URL vs Name)
    if (inputModeButtons && inputModeButtons.length) {
        inputModeButtons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var mode = btn.getAttribute("data-mode") || "url";
                inputMode = mode;
                inputModeButtons.forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");

                if (mode === "name") {
                    urlInput.placeholder = "輸入餐廳名稱或關鍵字，例如「鼎泰豐 信義」";
                    if (inputHint) {
                        inputHint.textContent = "用店名找餐廳：會用 Google Maps 搜尋，幫你挑出最符合的一間再做評論分析。";
                    }
                } else {
                    urlInput.placeholder = "貼上 Google Maps 餐廳連結...";
                    if (inputHint) {
                        inputHint.textContent = "支援格式：google.com/maps/place/... 或 maps.app.goo.gl/...";
                    }
                }

                urlInput.value = "";
                urlInput.focus();
            });
        });
    }

})();
