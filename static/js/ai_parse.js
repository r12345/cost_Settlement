/* AI 记账页：自然语言解析 → 草稿确认 → 落库。 */
(function () {
  "use strict";

  if (!API.requireAuth()) {
    return;
  }

  var root = document.getElementById("ai-page");
  var GROUP_ID = root.dataset.groupId;
  var state = { members: [], draft: null };

  function $(id) {
    return document.getElementById(id);
  }

  function memberName(userId) {
    var member = state.members.find(function (item) {
      return item.user_id === Number(userId);
    });
    return member ? member.nickname || member.username : "#" + userId;
  }

  function showFallback(reason) {
    var box = $("fallback-box");
    box.classList.remove("d-none");
    $("fallback-reason").textContent = reason || "无法从文本中解析出支出信息，请手动填写";
    if ((reason || "").indexOf("未启用") >= 0) {
      $("ai-disabled-box").classList.remove("d-none");
    }
  }

  function hideFallback() {
    $("fallback-box").classList.add("d-none");
  }

  function renderDraft(draft, confidence) {
    state.draft = draft;
    var list = $("draft-body");
    var rows = [
      ["付款人", memberName(draft.payer_id)],
      ["金额", API.money(draft.amount) + " 元"],
      [
        "参与人",
        (draft.participants || []).map(memberName).join("、"),
      ],
      ["分摊方式", draft.split_type],
      ["分类", draft.category],
      ["备注", draft.note || "-"],
      ["置信度", Number(confidence).toFixed(2)],
    ];
    list.innerHTML = rows
      .map(function (row) {
        return (
          "<tr><th class=\"text-muted\">" +
          escapeHtml(row[0]) +
          "</th><td>" +
          escapeHtml(row[1]) +
          "</td></tr>"
        );
      })
      .join("");
    $("draft-card").classList.remove("d-none");
  }

  $("parse-button").addEventListener("click", async function () {
    var text = $("ai-text").value.trim();
    if (!text) {
      showError("请先输入一句话描述这笔支出");
      return;
    }

    hideFallback();
    $("draft-card").classList.add("d-none");
    $("parse-button").disabled = true;

    try {
      var data = await API.post("/api/groups/" + GROUP_ID + "/expenses/parse/", {
        text: text,
      });
      renderDraft(data.draft, data.confidence);
    } catch (error) {
      showFallback(error.detail);
      showError(error.detail);
    } finally {
      $("parse-button").disabled = false;
    }
  });

  $("confirm-button").addEventListener("click", async function () {
    if (!state.draft) {
      return;
    }
    $("confirm-button").disabled = true;
    try {
      await API.post("/api/groups/" + GROUP_ID + "/expenses/from-draft/", state.draft);
      showSuccess("支出已由草稿创建");
      window.location.href = "/groups/" + GROUP_ID + "/";
    } catch (error) {
      showError(error.detail);
      $("confirm-button").disabled = false;
    }
  });

  (async function init() {
    try {
      var data = await API.get("/api/groups/" + GROUP_ID + "/balances/");
      state.members = data.balances || [];
    } catch (error) {
      showError(error.detail);
    }
  })();
})();
