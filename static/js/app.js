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

    // Input mode 已改為自動判斷：「像網址的就當 Google Maps 連結，其餘視為店名 / 關鍵字」
    var inputHint = document.getElementById("inputHint");

    // Name-search candidates
    var searchResultsPanel = document.getElementById("searchResultsPanel");
    var searchResultsList = document.getElementById("searchResultsList");
    var currentSearchResults = [];

    // Real map preview (Leaflet) for name-search results
    var placesMapWrap = document.getElementById("placesMapWrap");
    var placesMapEl = document.getElementById("placesMap");
    var placesMapSub = document.getElementById("placesMapSub");
    var placesMapInstance = null;
    var placesMarkersLayer = null;
    var lastSearchCenter = null; // {lat, lng} when geolocation is available

    // Place preview info card (Google Maps–like info panel)
    var placePreview = document.getElementById("placePreview");
    var placePreviewName = document.getElementById("placePreviewName");
    var placePreviewMeta = document.getElementById("placePreviewMeta");
    var placePreviewAddress = document.getElementById("placePreviewAddress");
    var placePreviewAnalyzeBtn = document.getElementById("placePreviewAnalyzeBtn");
    var placePreviewMapsBtn = document.getElementById("placePreviewMapsBtn");
    var selectedPlace = null;

    function ensurePlacesMap() {
        if (!placesMapEl || typeof L === "undefined") return false;
        if (placesMapInstance) return true;

        try {
            placesMapInstance = L.map(placesMapEl, {
                zoomControl: true,
                scrollWheelZoom: false,
                tap: true,
            });
            L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
                maxZoom: 19,
                attribution: "&copy; OpenStreetMap contributors",
            }).addTo(placesMapInstance);
            placesMarkersLayer = L.layerGroup().addTo(placesMapInstance);

            // Default view (Taipei) until we have user location or results
            placesMapInstance.setView([25.0330, 121.5654], 12);
            return true;
        } catch (e) {
            return false;
        }
    }

    function hidePlacePreview() {
        selectedPlace = null;
        if (placePreview) placePreview.classList.add("hidden");
    }

    function clearPlacesMap() {
        if (placesMapWrap) placesMapWrap.classList.add("hidden");
        if (placesMarkersLayer) {
            try { placesMarkersLayer.clearLayers(); } catch (e) { /* ignore */ }
        }
        lastSearchCenter = null;
        hidePlacePreview();
    }

    function showPlacePreview(item) {
        if (!item || !placePreview || !placePreviewName) return;
        selectedPlace = item;

        var title = item.name || "未命名地點";
        var addr = item.address || "";
        var rating = item.rating != null ? (item.rating.toFixed ? item.rating.toFixed(1) : item.rating) : null;
        var total = item.user_ratings_total;
        var parts = [];
        if (rating) parts.push("Google 評分 " + rating + "★");
        if (total != null) parts.push(total + " 則評論");

        placePreviewName.textContent = title;
        if (placePreviewMeta) placePreviewMeta.textContent = parts.join(" · ") || "";
        if (placePreviewAddress) placePreviewAddress.textContent = addr;

        placePreview.classList.remove("hidden");

        try {
            placePreview.scrollIntoView({ behavior: "smooth", block: "center" });
        } catch (e) {
            // ignore
        }
    }

    function renderPlacesMap(results) {
        if (!placesMapWrap || !placesMapEl) return;
        if (!ensurePlacesMap()) return;

        // Only show map when we have at least 1 item with lat/lng
        var hasGeo = (results || []).some(function (r) {
            return r && typeof r.lat === "number" && typeof r.lng === "number";
        });
        if (!hasGeo) {
            placesMapWrap.classList.add("hidden");
            return;
        }

        placesMapWrap.classList.remove("hidden");

        // Leaflet needs size invalidate when container toggles visibility
        setTimeout(function () {
            try { placesMapInstance.invalidateSize(); } catch (e) { /* ignore */ }
        }, 60);

        try { placesMarkersLayer.clearLayers(); } catch (e) { /* ignore */ }

        var bounds = [];

        // User location marker (optional)
        if (lastSearchCenter && typeof lastSearchCenter.lat === "number" && typeof lastSearchCenter.lng === "number") {
            var userMarker = L.circleMarker([lastSearchCenter.lat, lastSearchCenter.lng], {
                radius: 6,
                color: "#1a73e8",
                fillColor: "#1a73e8",
                fillOpacity: 0.75,
                weight: 2,
            }).addTo(placesMarkersLayer);
            userMarker.bindTooltip("你的位置", { direction: "top", offset: [0, -6] });
            bounds.push([lastSearchCenter.lat, lastSearchCenter.lng]);
        }

        (results || []).forEach(function (item) {
            if (!item || typeof item.lat !== "number" || typeof item.lng !== "number") return;

            var marker = L.marker([item.lat, item.lng]).addTo(placesMarkersLayer);
            var title = item.name || "未命名地點";
            var addr = item.address || "";
            var html = "<div style='font-weight:700;margin-bottom:2px;'>" + title + "</div>";
            if (addr) html += "<div style='font-size:12px;opacity:.85;'>" + addr + "</div>";
            marker.bindPopup(html);

            marker.on("click", function () {
                // Clicking a pin now shows a Google Maps–style info card first
                showPlacePreview(item);
            });
            bounds.push([item.lat, item.lng]);
        });

        if (bounds.length) {
            try {
                placesMapInstance.fitBounds(bounds, { padding: [18, 18] });
            } catch (e) {
                // ignore
            }
        }
    }

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
    // Model toggle（僅保留快速模式，後端也只跑一種模型）
    // ---------------------------------------------------------------------------
    var selectedModel = "gemini-3-flash-preview";

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
        // 回到初始狀態時隱藏手機版「地圖殼」
        try {
            var mapShell = document.querySelector(".mobile-map-shell");
            if (mapShell) {
                mapShell.classList.add("hidden");
            }
        } catch (e) {
            // ignore
        }
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

    function clearSearchResults() {
        currentSearchResults = [];
        if (searchResultsList) searchResultsList.innerHTML = "";
        if (searchResultsPanel) searchResultsPanel.classList.add("hidden");
        clearPlacesMap();
    }

    function renderSearchResults(list) {
        if (!searchResultsPanel || !searchResultsList) return;
        searchResultsList.innerHTML = "";
        currentSearchResults = list || [];

        if (!currentSearchResults.length) {
            var empty = document.createElement("div");
            empty.className = "search-result-empty";
            empty.textContent = "找不到符合的餐廳，請試著加上地區或更完整的店名。";
            searchResultsList.appendChild(empty);
            searchResultsPanel.classList.remove("hidden");
            return;
        }

        currentSearchResults.forEach(function (item, idx) {
            var row = document.createElement("button");
            row.type = "button";
            row.className = "search-result-item";
            row.addEventListener("click", function () {
                // 選擇列表中的店家時，先顯示資訊卡，再由使用者決定是否開始分析
                showPlacePreview(item);
            });

            var title = document.createElement("div");
            title.className = "search-result-title";
            title.textContent = item.name || "未命名地點";

            var addr = document.createElement("div");
            addr.className = "search-result-address";
            addr.textContent = item.address || "";

            var meta = document.createElement("div");
            meta.className = "search-result-meta";
            var rating = item.rating != null ? item.rating.toFixed ? item.rating.toFixed(1) : item.rating : null;
            var total = item.user_ratings_total;
            var parts = [];
            if (rating) parts.push("Google 評分 " + rating + "★");
            if (total != null) parts.push(total + " 則評論");
            meta.textContent = parts.join(" · ");

            row.appendChild(title);
            if (addr.textContent) row.appendChild(addr);
            if (meta.textContent) row.appendChild(meta);
            searchResultsList.appendChild(row);
        });

        searchResultsPanel.classList.remove("hidden");

        // Also show results on the real map (if coordinates are available)
        renderPlacesMap(currentSearchResults);
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
        var name = data.restaurant_name || "餐廳分析結果";
        var analyzedCount = data.total_reviews_analyzed || 0;

        document.getElementById("restaurantName").textContent = name;
        document.getElementById("reviewCount").textContent =
            "已分析 " + analyzedCount + " 則評論";
        document.getElementById("restaurantIntro").textContent =
            data.restaurant_intro || data.dining_tips || "暫無餐廳介紹資訊。";

        // 分析完成後才顯示手機版「地圖殼」區塊
        try {
            var mapShell = document.querySelector(".mobile-map-shell");
            if (mapShell) {
                mapShell.classList.remove("hidden");
            }
        } catch (e) {
            // ignore
        }

        // Update mobile Google Maps–style fake map summary
        try {
            var poi = document.getElementById("mobileMapPoi");
            var nameEl = document.getElementById("mobilePoiName");
            var metaEl = document.getElementById("mobilePoiMeta");
            var hintEl = document.getElementById("mobileMapHint");
            var descEl = document.getElementById("mobileSheetDesc");

            if (poi && nameEl && metaEl) {
                nameEl.textContent = name;

                var rating = null;
                if (typeof data.google_rating === "number") {
                    rating = data.google_rating.toFixed(1) + "★";
                } else if (data.google_rating) {
                    rating = data.google_rating + "★";
                }

                var parts = [];
                if (rating) parts.push("Google 評分 " + rating);
                if (analyzedCount) parts.push("分析 " + analyzedCount + " 則評論");
                metaEl.textContent = parts.join(" · ") || "已完成評論分析";

                poi.style.display = "block";
                if (hintEl) hintEl.style.display = "none";

                if (descEl) {
                    descEl.textContent = "你目前正在查看「" + name + "」的評論分析，向下滑即可閱讀完整圖表與重點整理。";
                }
            }
        } catch (e) {
            // 軟性失敗：即使 mobile map 區塊不存在也不影響桌機版
        }
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

        var tasteScore = data.taste && typeof data.taste.score === "number" ? data.taste.score : 0;
        var serviceScore = data.service && typeof data.service.score === "number" ? data.service.score : 0;
        var envScore = data.environment && typeof data.environment.score === "number" ? data.environment.score : 0;
        var valueScore = data.value_for_money && typeof data.value_for_money.score === "number" ? data.value_for_money.score : 0;

        var scores = [tasteScore, serviceScore, envScore, valueScore];

        // 在雷達圖標籤旁邊直接顯示各維度分數（例如「口味 8.2」）
        function formatLabel(label, score) {
            if (score == null || isNaN(score)) return label;
            return label + " " + score.toFixed(1);
        }

        var labels = [
            formatLabel("口味", tasteScore),
            formatLabel("服務", serviceScore),
            formatLabel("環境", envScore),
            formatLabel("CP值", valueScore),
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
                labels: labels,
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
    // Render: Photo Gallery (grouped by category)
    // ---------------------------------------------------------------------------
    function renderFoodPhotos(photoData) {
        var section = document.getElementById("foodPhotoSection");
        var strip = document.getElementById("photoStrip");
        var empty = document.getElementById("photoEmpty");
        var tabs = document.querySelectorAll(".photo-tab");

        if (!section || !strip) return;

        if (!photoData) {
            hide(section);
            return;
        }

        var groups;
        if (Array.isArray(photoData)) {
            // Backward compatibility: old flat array -> 全部歸類到「食物」
            groups = {
                food: photoData,
                environment: [],
                menu: [],
            };
        } else {
            groups = {
                food: photoData.food || [],
                environment: photoData.environment || [],
                menu: photoData.menu || [],
            };
        }

        var hasAny =
            (groups.food && groups.food.length) ||
            (groups.environment && groups.environment.length) ||
            (groups.menu && groups.menu.length);

        if (!hasAny) {
            hide(section);
            return;
        }

        show(section);

        function setActiveTab(category) {
            if (!tabs || !tabs.length) return;
            tabs.forEach(function (tab) {
                var cat = tab.getAttribute("data-category");
                if (cat === category) {
                    tab.classList.add("active");
                } else {
                    tab.classList.remove("active");
                }
            });
        }

        function renderCategory(category) {
            var list = groups[category] || [];
            strip.innerHTML = "";
            if (!list.length) {
                strip.style.display = "none";
                if (empty) empty.style.display = "block";
                return;
            }
            strip.style.display = "flex";
            if (empty) empty.style.display = "none";
            list.forEach(function (url) {
                var item = document.createElement("div");
                item.className = "photo-item";

                var img = document.createElement("img");
                img.className = "gallery-photo";
                img.src = url;
                img.alt = "餐廳照片";
                img.loading = "lazy";
                img.onerror = function () { item.style.display = "none"; };
                img.onclick = function () { openLightbox(url); };

                item.appendChild(img);
                strip.appendChild(item);
            });
        }

        // Decide initial active category：優先顯示有內容的
        var activeCategory = "food";
        if (!groups.food.length && groups.environment.length) {
            activeCategory = "environment";
        } else if (!groups.food.length && !groups.environment.length && groups.menu.length) {
            activeCategory = "menu";
        }

        setActiveTab(activeCategory);
        renderCategory(activeCategory);

        // Bind tab click events once
        if (!renderFoodPhotos._bound) {
            tabs.forEach(function (tab) {
                tab.addEventListener("click", function () {
                    var cat = tab.getAttribute("data-category");
                    setActiveTab(cat);
                    renderCategory(cat);
                });
            });
            renderFoodPhotos._bound = true;
        }
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
        text += "四大指標評分（1–10）：\n";
        text += "口味：" + (d.taste ? d.taste.score : "?") + "，";
        text += "服務：" + (d.service ? d.service.score : "?") + "，";
        text += "環境：" + (d.environment ? d.environment.score : "?") + "，";
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
        var t = lastAnalysisData;
        var shareText = "食神｜" + name + " Google Maps 評論分析，四大指標："
            + "口味 " + (t.taste ? t.taste.score : "?")
            + "・服務 " + (t.service ? t.service.score : "?")
            + "・環境 " + (t.environment ? t.environment.score : "?")
            + "・CP值 " + (t.value_for_money ? t.value_for_money.score : "?")
            + "（滿分 10 分）";
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
        renderFoodPhotos(data.photo_groups || data.food_photos);
        renderSceneRecommendations(data.scene_recommendations);
        renderBestVisitTime(data.best_visit_time);
        renderRatingTrend(data.rating_trend);
    }

    // ---------------------------------------------------------------------------
    // Main analysis flow
    // ---------------------------------------------------------------------------
    function runAnalyze(url, displayNameOverride) {
        lastAnalyzedUrl = url;
        analyzeBtn.classList.add("loading");
        analyzeBtn.disabled = true;
        hide(errorSection);
        hide(resultsSection);
        hide(document.getElementById("fakeReviewSection"));
        hide(document.getElementById("foodPhotoSection"));
        clearSearchResults();
        // 新一次分析開始前，避免顯示上一間店的地圖卡片
        try {
            var mapShell = document.querySelector(".mobile-map-shell");
            if (mapShell) {
                mapShell.classList.add("hidden");
            }
        } catch (e) {
            // ignore
        }
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
                var displayName = data.restaurant_name || displayNameOverride || "未知餐廳";
                saveToHistory(displayName, url);

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

    function startAnalysis() {
        var raw = urlInput.value.trim();
        if (!raw) {
            urlInput.focus();
            urlInput.style.borderColor = "#ea4335";
            setTimeout(function () { urlInput.style.borderColor = ""; }, 1500);
            return;
        }

        // 先判斷是不是看起來像 Google Maps 連結，是的話直接走網址分析
        if (isValidUrl(raw)) {
            runAnalyze(raw);
            return;
        }

        // 其餘情況一律視為「店名 / 關鍵字」
        analyzeBtn.classList.add("loading");
        analyzeBtn.disabled = true;
        hide(errorSection);
        hide(resultsSection);
        hide(loadingSection);
        hide(skeletonSection);

        clearSearchResults();

        // 優先嘗試取得使用者所在位置，幫忙把最近的分店排在前面；
        // 若使用者拒絕或瀏覽器不支援，就退回純文字搜尋。
            function doSearch(payload) {
            // Keep the last geolocation center (best-effort) for map preview
            try {
                if (payload && payload.user_lat != null && payload.user_lng != null) {
                    lastSearchCenter = { lat: Number(payload.user_lat), lng: Number(payload.user_lng) };
                } else {
                    lastSearchCenter = null;
                }
            } catch (e) {
                lastSearchCenter = null;
            }

            fetch("/api/search_places", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            })
                .then(function (res) {
                    if (!res.ok) {
                        return res.json().then(function (body) {
                            throw new Error(body.error || "搜尋餐廳失敗，請稍後再試");
                        });
                    }
                    return res.json();
                })
                .then(function (data) {
                    analyzeBtn.classList.remove("loading");
                    analyzeBtn.disabled = false;
                    renderSearchResults(data.results || []);
                })
                .catch(function (err) {
                    analyzeBtn.classList.remove("loading");
                    analyzeBtn.disabled = false;
                    showError(err.message || "搜尋餐廳失敗，請稍後再試");
                });
        }

        var basePayload = { query: raw, limit: 6 };

            // 這裡改成「完全不抓使用者定位」：一律用你輸入的文字去找店，
            // 再由你從列表中選擇正確的分店。
            doSearch(basePayload);
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

    // 輸入模式：像網址的就當 Google Maps 連結，其餘視為店名 / 關鍵字，
    // 不再使用你的定位，只用你輸入的文字來找店。
    if (urlInput && inputHint) {
        inputHint.textContent = "支援貼上 Google Maps 網址與店名搜尋：貼網址會直接開始分析，輸入店名會先列出店家資訊卡，讓你確認後再開始分析。";
    }

    // 綁定店家資訊卡上的按鈕：開始分析 / 在 Google Maps 開啟
    if (placePreviewAnalyzeBtn) {
        placePreviewAnalyzeBtn.addEventListener("click", function () {
            if (selectedPlace && selectedPlace.maps_url) {
                runAnalyze(selectedPlace.maps_url, selectedPlace.name || "");
                clearSearchResults();
                hidePlacePreview();
            }
        });
    }

    if (placePreviewMapsBtn) {
        placePreviewMapsBtn.addEventListener("click", function () {
            if (selectedPlace && selectedPlace.maps_url) {
                window.open(selectedPlace.maps_url, "_blank");
            }
        });
    }

    // ---------------------------------------------------------------------------
    // Sidebar navigation
    // ---------------------------------------------------------------------------
    var sidebar = document.querySelector(".sidebar");
    var sidebarToggle = document.getElementById("sidebarToggle");
    var sidebarBackdrop = document.getElementById("sidebarBackdrop");

    function closeSidebar() {
        if (sidebar) {
            sidebar.classList.remove("sidebar-open");
        }
        if (sidebarBackdrop) {
            sidebarBackdrop.classList.remove("active");
        }
        document.body.style.overflow = "";
    }

    function openSidebar() {
        if (sidebar) {
            sidebar.classList.add("sidebar-open");
        }
        if (sidebarBackdrop) {
            sidebarBackdrop.classList.add("active");
        }
        document.body.style.overflow = "hidden";
    }

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener("click", function () {
            if (sidebar.classList.contains("sidebar-open")) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });
    }

    if (sidebarBackdrop) {
        sidebarBackdrop.addEventListener("click", closeSidebar);
    }

    var navButtons = document.querySelectorAll(".nav-item");
    var pages = {
        home: document.getElementById("page-home"),
        sample: document.getElementById("page-sample"),
        about: document.getElementById("page-about"),
        legal: document.getElementById("page-legal"),
    };

    if (navButtons && navButtons.length) {
        navButtons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var target = btn.getAttribute("data-page");
                navButtons.forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                Object.keys(pages).forEach(function (key) {
                    var page = pages[key];
                    if (!page) return;
                    if (key === target) {
                        page.classList.add("page-active");
                    } else {
                        page.classList.remove("page-active");
                    }
                });

                // Close sidebar on mobile after navigation
                if (window.innerWidth <= 768) {
                    closeSidebar();
                }
            });
        });
    }

})();
