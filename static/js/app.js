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

    // ---------------------------------------------------------------------------
    // URL validation (client-side)
    // ---------------------------------------------------------------------------
    const URL_PATTERNS = [
        /https?:\/\/(www\.)?google\.(com|com\.\w{2})\/maps\/place\//i,
        /https?:\/\/maps\.app\.goo\.gl\//i,
        /https?:\/\/goo\.gl\/maps\//i,
    ];

    function isValidUrl(url) {
        return URL_PATTERNS.some(function (p) { return p.test(url); });
    }

    // ---------------------------------------------------------------------------
    // UI helpers
    // ---------------------------------------------------------------------------
    function show(el) { el.classList.remove("hidden"); }
    function hide(el) { el.classList.add("hidden"); }

    function setStep(activeStep) {
        [step1, step2, step3].forEach(function (s, i) {
            s.classList.remove("active", "done");
            if (i + 1 < activeStep) s.classList.add("done");
            if (i + 1 === activeStep) s.classList.add("active");
        });
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

    function showError(msg) {
        hide(loadingSection);
        hide(skeletonSection);
        hide(resultsSection);
        errorMessage.textContent = msg;
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
            gallery.appendChild(img);
        });
    }

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

        analyzeBtn.classList.add("loading");
        analyzeBtn.disabled = true;
        hide(errorSection);
        hide(resultsSection);
        hide(document.getElementById("fakeReviewSection"));
        hide(document.getElementById("foodPhotoSection"));
        show(loadingSection);
        show(skeletonSection);
        setStep(1);

        var stepTimer2 = setTimeout(function () { setStep(2); }, 8000);
        var stepTimer3 = setTimeout(function () { setStep(3); }, 30000);

        // 5-minute timeout to accommodate scraping + AI analysis + Vision
        var controller = new AbortController();
        var fetchTimeout = setTimeout(function () { controller.abort(); }, 300000);

        fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: url }),
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

                hide(loadingSection);
                hide(skeletonSection);
                show(resultsSection);
                analyzeBtn.classList.remove("loading");
                analyzeBtn.disabled = false;

                // Render in order: Intro → Dims → Fake Warning → Dishes → Photos
                renderIntro(data);
                renderOverviewAndDimensions(data);
                renderFakeWarning(data.fake_review_detection);
                renderDishes(data);
                renderFoodPhotos(data.food_photos);

                resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
            })
            .catch(function (err) {
                clearTimeout(fetchTimeout);
                clearTimeout(stepTimer2);
                clearTimeout(stepTimer3);
                var msg = err.name === "AbortError"
                    ? "分析請求逾時, 請稍後再試."
                    : (err.message || "發生未知錯誤, 請稍後再試.");
                showError(msg);
            });
    }

    // ---------------------------------------------------------------------------
    // Event listeners
    // ---------------------------------------------------------------------------
    analyzeBtn.addEventListener("click", startAnalysis);

    urlInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") startAnalysis();
    });

    // ---------------------------------------------------------------------------
    // PWA disabled to avoid stale-cache issues
    // ---------------------------------------------------------------------------
})();
