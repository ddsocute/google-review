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
    var radarChartInstance = null;

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
        /https?:\/\/(www\.)?google\.(com|com\.\w{2})\/maps\/place\//i,
        /https?:\/\/(www\.)?google\.(com|com\.\w{2})\/maps/i,
        /https?:\/\/(maps\.)?google\.(com|com\.\w{2})\/maps/i,
        /https?:\/\/maps\.app\.goo\.gl\//i,
        /https?:\/\/goo\.gl\/maps\//i,
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

        // Set appropriate icon based on error type
        var title = document.getElementById("errorTitle");
        if (msg.includes("逾時")) {
            document.querySelector(".error-icon").textContent = "⏰";
            title.textContent = "分析逾時";
        } else if (msg.includes("額度")) {
            document.querySelector(".error-icon").textContent = "💳";
            title.textContent = "額度不足";
        } else if (msg.includes("找到") || msg.includes("沒有")) {
            document.querySelector(".error-icon").textContent = "🔍";
            title.textContent = "找不到評論";
        } else {
            document.querySelector(".error-icon").textContent = "😥";
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

        // Destroy old chart if exists
        if (radarChartInstance) {
            radarChartInstance.destroy();
            radarChartInstance = null;
        }

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
                    pointBorderColor: "#fff",
                    pointBorderWidth: 2,
                    pointRadius: 5,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false },
                },
                scales: {
                    r: {
                        min: 0,
                        max: 10,
                        ticks: {
                            stepSize: 2,
                            font: { size: 11 },
                            backdropColor: "transparent",
                        },
                        pointLabels: {
                            font: { size: 14, weight: "bold", family: "'Noto Sans TC', sans-serif" },
                            color: "#202124",
                        },
                        grid: { color: "rgba(0,0,0,0.06)" },
                        angleLines: { color: "rgba(0,0,0,0.06)" },
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
        setTimeout(function () {
            el.style.width = (score * 10) + "%";
        }, 200);
    }

    // ---------------------------------------------------------------------------
    // Render: Fake Review Warning with Timeline (Section 3)
    // ---------------------------------------------------------------------------
    function renderFakeWarning(detection) {
        if (!detection) return;
        var pct = detection.percentage || 0;
        if (pct <= 0 && detection.suspected_count <= 0) return;

        var section = document.getElementById("fakeReviewSection");
        var card = document.getElementById("fakeCard");
        show(section);

        if (pct > 15) {
            card.className = "fake-card level-high";
        } else {
            card.className = "fake-card level-low";
        }

        document.getElementById("fakeBadge").textContent = detection.warning_level || "注意";
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

        // Activity Timeline
        var timelineEl = document.getElementById("fakeTimeline");
        timelineEl.innerHTML = "";
        var ap = detection.activity_period;
        if (ap) {
            var tl = document.createElement("div");
            tl.className = "timeline-box";

            var tlTitle = document.createElement("div");
            tlTitle.className = "timeline-title";
            tlTitle.innerHTML = "📅 活動時間軸";
            tl.appendChild(tlTitle);

            var tlRow = document.createElement("div");
            tlRow.className = "timeline-row";

            var startBlock = document.createElement("div");
            startBlock.className = "timeline-item";
            startBlock.innerHTML =
                '<span class="tl-dot start"></span>' +
                '<span class="tl-label">開始</span>' +
                '<span class="tl-date">' + (ap.start_date || "不明") + '</span>';
            tlRow.appendChild(startBlock);

            var arrow = document.createElement("div");
            arrow.className = "timeline-arrow";
            arrow.innerHTML = "→";
            tlRow.appendChild(arrow);

            var endBlock = document.createElement("div");
            endBlock.className = "timeline-item";
            endBlock.innerHTML =
                '<span class="tl-dot ' + (ap.is_ongoing ? "ongoing" : "end") + '"></span>' +
                '<span class="tl-label">' + (ap.is_ongoing ? "進行中" : "結束") + '</span>' +
                '<span class="tl-date">' + (ap.end_date || "不明") + '</span>';
            tlRow.appendChild(endBlock);

            tl.appendChild(tlRow);

            var statusBadge = document.createElement("div");
            statusBadge.className = "timeline-status " + (ap.is_ongoing ? "status-ongoing" : "status-ended");
            statusBadge.textContent = ap.is_ongoing ? "🔴 目前仍在進行中" : "✅ 活動已結束";
            tl.appendChild(statusBadge);

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
    // Render: Dishes (Section 4 & 5)
    // ---------------------------------------------------------------------------
    function createDishCard(dish, isGood) {
        var card = document.createElement("div");
        card.className = "dish-card";

        // No photos on individual dish cards

        var info = document.createElement("div");
        info.className = "dish-info";

        var nameRow = document.createElement("div");
        nameRow.className = "dish-name-row";

        var nameEl = document.createElement("span");
        nameEl.className = "dish-name";
        nameEl.textContent = (isGood ? "👍 " : "👎 ") + dish.name;
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
            recDishes.forEach(function (d) {
                recContainer.appendChild(createDishCard(d, true));
            });
        }

        if (notDishes.length === 0) {
            notContainer.innerHTML = '<p class="no-dishes">評論中未明確提到特定不推薦菜色</p>';
        } else {
            notDishes.forEach(function (d) {
                notContainer.appendChild(createDishCard(d, false));
            });
        }
    }

    // ---------------------------------------------------------------------------
    // Render: Food Photo Gallery
    // ---------------------------------------------------------------------------
    function renderFoodPhotos(photos) {
        var section = document.getElementById("foodPhotoSection");
        var gallery = document.getElementById("photoGallery");
        if (!photos || photos.length === 0) {
            hide(section);
            return;
        }
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
    // Main analysis flow
    // ---------------------------------------------------------------------------
    function startAnalysis() {
        var url = urlInput.value.trim();
        if (!url) {
            urlInput.focus();
            urlInput.style.borderColor = "#ea4335";
            setTimeout(function () { urlInput.style.borderColor = ""; }, 1500);
            return;
        }

        if (!isValidUrl(url)) {
            showError("網址格式不正確, 請貼上 Google Maps 餐廳連結.");
            return;
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
        setProgress(5, "正在連接 Google Maps...");

        // Animated progress simulation
        var progressPercent = 5;
        var progressInterval = setInterval(function () {
            if (progressPercent < 25) {
                progressPercent += 1;
                setProgress(progressPercent, "正在抓取評論資料...");
            } else if (progressPercent < 45) {
                progressPercent += 0.3;
                setProgress(Math.round(progressPercent), "評論資料處理中...");
            } else if (progressPercent < 80) {
                progressPercent += 0.15;
                setProgress(Math.round(progressPercent), "AI 正在分析中，請耐心等候...");
            } else if (progressPercent < 95) {
                progressPercent += 0.05;
                setProgress(Math.round(progressPercent), "快完成了...");
            }
        }, 500);

        var stepTimer2 = setTimeout(function () { setStep(2); setProgress(30, "AI 正在深度分析評論..."); }, 8000);
        var stepTimer3 = setTimeout(function () { setStep(3); setProgress(70, "正在整理分析報告..."); }, 30000);

        // 5-minute timeout to accommodate scraping + AI analysis + Vision
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
                clearInterval(progressInterval);
                if (!res.ok) {
                    return res.json().then(function (body) {
                        throw new Error(body.error || "伺服器錯誤 (" + res.status + ")");
                    });
                }
                return res.json();
            })
            .then(function (data) {
                if (data.error) throw new Error(data.error);

                setProgress(100, "分析完成！");

                setTimeout(function () {
                    hide(loadingSection);
                    hide(skeletonSection);
                    show(resultsSection);
                    analyzeBtn.classList.remove("loading");
                    analyzeBtn.disabled = false;

                    // Render in order: Intro → Radar → Dims → Fake Warning → Dishes → Photos
                    renderIntro(data);
                    renderRadarChart(data);
                    renderOverviewAndDimensions(data);
                    renderFakeWarning(data.fake_review_detection);
                    renderDishes(data);
                    renderFoodPhotos(data.food_photos);

                    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
                }, 400);
            })
            .catch(function (err) {
                clearTimeout(fetchTimeout);
                clearTimeout(stepTimer2);
                clearTimeout(stepTimer3);
                clearInterval(progressInterval);
                var msg = err.name === "AbortError"
                    ? "分析請求逾時（超過 5 分鐘），請稍後再試或切換到快速模式"
                    : (err.message || "發生未知錯誤, 請稍後再試.");
                showError(msg);
            });
    }

    // ---------------------------------------------------------------------------
    // Demo: pre-stored example result
    // ---------------------------------------------------------------------------
    var DEMO_DATA = {
        restaurant_name: "鼎泰豐（信義店）",
        restaurant_intro: "鼎泰豐信義店位於台北 101 購物中心地下一樓，是全球知名的小籠包專賣店。以精緻的手工小籠包聞名，每顆小籠包皮薄餡多，湯汁飽滿。除了招牌小籠包外，還提供各式蒸餃、炒飯、麵食及甜品。餐廳環境整潔明亮，開放式廚房讓客人可以欣賞師傅精湛的手藝。服務態度親切有禮，適合家庭聚餐、朋友聚會及觀光客體驗台灣美食。建議平日前往可減少等候時間，假日尖峰時段等位可能需要 30-60 分鐘。",
        total_reviews_analyzed: 60,
        overall_score: 8.2,
        taste: { score: 8.5, summary: "口味方面獲得一致好評，小籠包皮薄餡多、湯汁鮮美，多位顧客表示是他們吃過最好的小籠包。炒飯粒粒分明，蝦仁口感彈牙。部分評論提到口味偏清淡，但整體品質穩定。", positive_keywords: ["皮薄餡多", "湯汁鮮美", "口感細緻", "食材新鮮"], negative_keywords: ["偏清淡"] },
        service: { score: 8.0, summary: "服務態度普遍受到好評，服務員親切有禮、反應迅速。出餐速度快，桌面整潔度維持良好。少數時段因人潮擁擠，服務品質略有波動。", positive_keywords: ["態度親切", "出餐快速", "專業"], negative_keywords: ["尖峰時段較忙"] },
        environment: { score: 7.5, summary: "餐廳位於 101 地下美食街，環境整潔現代。開放式廚房是一大特色，可觀賞製作過程。座位間距稍嫌擁擠，用餐尖峰時段噪音較大。", positive_keywords: ["整潔明亮", "開放式廚房", "地點便利"], negative_keywords: ["座位偏擠", "假日擁擠"] },
        value_for_money: { score: 7.0, summary: "價格在觀光區餐廳中屬中上水準，但考量到食材品質和品牌價值，多數顧客認為物有所值。小籠包單價偏高，但份量和品質有保障。", positive_keywords: ["品質穩定", "物有所值"], negative_keywords: ["價格偏高"], price_range: "每人約 $400-800" },
        recommended_dishes: [
            { name: "小籠包", mentions: 45, reason: "鼎泰豐的招牌之王，18 褶的精緻工藝，皮薄如紙卻不破，一口咬下湯汁飽滿鮮甜。搭配薑絲和醋食用更添風味，幾乎每桌必點。", keywords: ["18 褶", "皮薄餡多", "湯汁飽滿", "必點"] },
            { name: "蝦仁炒飯", mentions: 22, reason: "粒粒分明的炒飯搭配新鮮彈牙的蝦仁，鍋氣十足。調味恰到好處，不油不膩，是小籠包以外最受歡迎的單品。", keywords: ["粒粒分明", "鍋氣足", "蝦仁彈牙"] },
            { name: "紅油抄手", mentions: 15, reason: "紅油香辣適中，餛飩皮滑餡嫩，花生碎增添口感層次。適合喜歡微辣的人，搭配小籠包組合超滿足。", keywords: ["辣度適中", "口感滑嫩", "層次豐富"] },
            { name: "芋泥小籠包", mentions: 12, reason: "甜點版小籠包，芋泥細緻綿密，甜而不膩。外皮同樣精緻，是用餐尾聲的完美句點。", keywords: ["甜而不膩", "芋泥綿密", "創意甜點"] }
        ],
        not_recommended_dishes: [
            { name: "酸辣湯", mentions: 5, reason: "多位顧客反映酸辣湯味道偏淡，缺乏層次感，與外面專賣店相比差距明顯。湯料豐富但調味不夠突出。", keywords: ["味道偏淡", "缺乏層次"] }
        ],
        fake_review_detection: {
            suspected_count: 3,
            total_reviews: 60,
            percentage: 5,
            reasons: ["觀光客打卡評論", "短評較多"],
            warning_level: "低度注意",
            details: "少數評論為觀光客打卡式短評，內容較空洞但非惡意灌水，整體評論品質良好。",
            activity_period: { start_date: "持續性", end_date: "至今", is_ongoing: true, description: "作為觀光熱點，持續有觀光客留下簡短的打卡式評論，但比例不高，不影響整體評論可信度。" }
        },
        food_photos: []
    };

    function loadDemo() {
        hide(errorSection);
        hide(loadingSection);
        hide(skeletonSection);
        show(resultsSection);
        analyzeBtn.classList.remove("loading");
        analyzeBtn.disabled = false;
        urlInput.value = "https://maps.app.goo.gl/demo";

        renderIntro(DEMO_DATA);
        renderRadarChart(DEMO_DATA);
        renderOverviewAndDimensions(DEMO_DATA);
        renderFakeWarning(DEMO_DATA.fake_review_detection);
        renderDishes(DEMO_DATA);
        renderFoodPhotos(DEMO_DATA.food_photos);

        resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // ---------------------------------------------------------------------------
    // Download report as image
    // ---------------------------------------------------------------------------
    window.downloadReport = function () {
        var btn = document.getElementById("downloadBtn");
        btn.textContent = "⏳ 產生圖片中...";
        btn.disabled = true;

        // Use html2canvas if available
        if (typeof html2canvas === "undefined") {
            btn.textContent = "📸 下載分析報告圖片";
            btn.disabled = false;
            alert("圖片產生元件載入失敗，請重新整理頁面後再試");
            return;
        }

        html2canvas(resultsSection, {
            scale: 2,
            useCORS: true,
            backgroundColor: "#ffffff",
            logging: false,
            windowWidth: 860,
        }).then(function (canvas) {
            var link = document.createElement("a");
            var name = (document.getElementById("restaurantName").textContent || "分析報告").replace(/[\/\\:]/g, "_");
            link.download = name + "-AI分析報告.png";
            link.href = canvas.toDataURL("image/png");
            link.click();
            btn.textContent = "📸 下載分析報告圖片";
            btn.disabled = false;
        }).catch(function () {
            btn.textContent = "📸 下載分析報告圖片";
            btn.disabled = false;
            alert("圖片產生失敗，請重試");
        });
    };

    // ---------------------------------------------------------------------------
    // Event listeners
    // ---------------------------------------------------------------------------
    analyzeBtn.addEventListener("click", startAnalysis);

    urlInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") startAnalysis();
    });

    var demoBtn = document.getElementById("demoBtn");
    if (demoBtn) {
        demoBtn.addEventListener("click", loadDemo);
    }

    // ---------------------------------------------------------------------------
    // PWA disabled to avoid stale-cache issues
    // ---------------------------------------------------------------------------
})();
