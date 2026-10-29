/* 群组详情页：支出 / 余额 / 结算 三个 Tab。 */
(function () {
  "use strict";

  if (!API.requireAuth()) {
    return;
  }

  var root = document.getElementById("group-detail");
  var GROUP_ID = root.dataset.groupId;
  var state = { members: [], expenses: [], balances: null, transfers: [] };

  var CATEGORY_LABELS = {
    FOOD: "餐饮",
    TRANSPORT: "交通",
    ACCOMMODATION: "住宿",
    ENTERTAINMENT: "娱乐",
    OTHER: "其他",
  };
  var SPLIT_LABELS = { EQUAL: "等额", EXACT: "精确", PERCENTAGE: "百分比" };

  function $(id) {
    return document.getElementById(id);
  }

  function displayName(member) {
    return member.nickname || member.username;
  }

  function memberName(userId) {
    var member = state.members.find(function (item) {
      return item.user_id === Number(userId);
    });
    return member ? displayName(member) : "#" + userId;
  }

  /* ---------------- 加载 ---------------- */
  async function loadGroup() {
    var group = await API.get("/api/groups/" + GROUP_ID + "/");
    $("group-name").textContent = group.name;
    $("group-member-count").textContent = group.member_count;
  }

  async function loadBalances() {
    var data = await API.get("/api/groups/" + GROUP_ID + "/balances/");
    state.members = data.balances || [];
    state.balances = data;
    renderMemberPickers();
    renderBalances();
  }

  async function loadExpenses() {
    var data = await API.get("/api/groups/" + GROUP_ID + "/expenses/?page_size=100");
    state.expenses = data.results || [];
    renderExpenses();
  }

  async function loadSuggestions() {
    var data = await API.get("/api/groups/" + GROUP_ID + "/settle/");
    state.transfers = data.transfers || [];
    renderSuggestions();
  }

  async function loadSettlements() {
    var data = await API.get("/api/groups/" + GROUP_ID + "/settlements/?page_size=100");
    renderSettlements(data.results || []);
  }

  async function refreshAll() {
    await loadBalances();
    await loadExpenses();
    await loadSuggestions();
    await loadSettlements();
  }

  /* ---------------- 支出列表 ---------------- */
  function renderExpenses() {
    var tbody = $("expense-rows");
    tbody.innerHTML = "";
    $("expense-empty").classList.toggle("d-none", state.expenses.length > 0);

    state.expenses.forEach(function (expense) {
      var row = document.createElement("tr");
      row.innerHTML =
        "<td>" +
        escapeHtml((expense.created_at || "").slice(0, 10)) +
        "</td>" +
        "<td>" +
        escapeHtml(expense.payer.username) +
        "</td>" +
        '<td class="text-end">' +
        API.money(expense.amount) +
        "</td>" +
        "<td>" +
        escapeHtml(SPLIT_LABELS[expense.split_type] || expense.split_type) +
        "</td>" +
        "<td>" +
        escapeHtml(expense.note || "-") +
        "</td>" +
        '<td class="text-nowrap">' +
        '<button class="btn btn-sm btn-outline-secondary me-1" data-action="edit" data-id="' +
        expense.id +
        '">编辑</button>' +
        '<button class="btn btn-sm btn-outline-danger" data-action="delete" data-id="' +
        expense.id +
        '">删除</button>' +
        "</td>";
      tbody.appendChild(row);
    });
  }

  $("expense-rows").addEventListener("click", function (event) {
    var button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    var expenseId = Number(button.dataset.id);
    if (button.dataset.action === "edit") {
      openEditModal(expenseId);
    } else if (button.dataset.action === "delete") {
      deleteExpense(expenseId);
    }
  });

  /* ---------------- 记账表单 ---------------- */
  function renderMemberPickers() {
    var payerSelect = $("expense-payer");
    var participantList = $("participant-list");
    payerSelect.innerHTML = "";
    participantList.innerHTML = "";

    state.members.forEach(function (member) {
      var option = document.createElement("option");
      option.value = member.user_id;
      option.textContent = displayName(member);
      payerSelect.appendChild(option);

      var label = document.createElement("label");
      label.className = "form-check form-check-inline";
      label.innerHTML =
        '<input class="form-check-input participant-checkbox" type="checkbox" value="' +
        member.user_id +
        '" checked> <span class="form-check-label">' +
        escapeHtml(displayName(member)) +
        "</span>";
      participantList.appendChild(label);
    });
  }

  function renderSplitInputs() {
    var splitType = $("expense-split-type").value;
    var box = $("split-inputs");
    box.innerHTML = "";

    if (splitType === "EQUAL") {
      box.classList.add("d-none");
      return;
    }
    box.classList.remove("d-none");

    var placeholder = splitType === "EXACT" ? "金额" : "百分比";
    var checked = Array.prototype.slice
      .call(document.querySelectorAll(".participant-checkbox:checked"))
      .map(function (checkbox) {
        return Number(checkbox.value);
      });

    checked.forEach(function (userId) {
      var row = document.createElement("div");
      row.className = "input-group input-group-sm mb-1";
      row.innerHTML =
        '<span class="input-group-text" style="width: 35%">' +
        escapeHtml(memberName(userId)) +
        "</span>" +
        '<input class="form-control split-value" type="number" step="0.01" min="0" data-user="' +
        userId +
        '" placeholder="' +
        placeholder +
        '">';
      box.appendChild(row);
    });
  }

  $("expense-split-type").addEventListener("change", renderSplitInputs);
  $("participant-list").addEventListener("change", renderSplitInputs);

  $("expense-form").addEventListener("submit", async function (event) {
    event.preventDefault();

    var splitType = $("expense-split-type").value;
    var participants = Array.prototype.slice
      .call(document.querySelectorAll(".participant-checkbox:checked"))
      .map(function (checkbox) {
        return Number(checkbox.value);
      });
    var amount = $("expense-amount").value;

    if (!participants.length) {
      showError("请至少选择一位参与人");
      return;
    }
    if (!amount || Number(amount) <= 0) {
      showError("金额必须大于 0");
      return;
    }

    var payload = {
      payer: Number($("expense-payer").value),
      amount: Number(amount).toFixed(2),
      split_type: splitType,
      participants: participants,
      note: $("expense-note").value.trim(),
      category: $("expense-category").value,
    };

    if (splitType !== "EQUAL") {
      var values = {};
      var missing = false;
      Array.prototype.slice
        .call(document.querySelectorAll(".split-value"))
        .forEach(function (input) {
          if (input.value === "") {
            missing = true;
          }
          values[input.dataset.user] = input.value;
        });
      if (missing) {
        showError(
          splitType === "EXACT" ? "请填写每个人的金额" : "请填写每个人的百分比"
        );
        return;
      }
      payload.splits = participants.map(function (userId) {
        var entry = { user: userId };
        if (splitType === "EXACT") {
          entry.share_amount = Number(values[userId]).toFixed(2);
        } else {
          entry.share_value = Number(values[userId]).toFixed(2);
        }
        return entry;
      });
    }

    try {
      await API.post("/api/groups/" + GROUP_ID + "/expenses/", payload);
      bootstrap.Modal.getInstance($("expense-modal")).hide();
      $("expense-form").reset();
      renderSplitInputs();
      showSuccess("支出已记录");
      await refreshAll();
    } catch (error) {
      showError(error.detail);
    }
  });

  /* ---------------- 编辑 / 删除 ---------------- */
  function openEditModal(expenseId) {
    var expense = state.expenses.find(function (item) {
      return item.id === expenseId;
    });
    if (!expense) {
      return;
    }
    $("edit-expense-id").value = expenseId;
    $("edit-amount").value = API.money(expense.amount);
    $("edit-note").value = expense.note || "";
    new bootstrap.Modal($("expense-edit-modal")).show();
  }

  $("expense-edit-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var expenseId = $("edit-expense-id").value;
    var amount = $("edit-amount").value;

    if (!amount || Number(amount) <= 0) {
      showError("金额必须大于 0");
      return;
    }

    try {
      await API.patch("/api/expenses/" + expenseId + "/", {
        amount: Number(amount).toFixed(2),
        note: $("edit-note").value.trim(),
      });
      bootstrap.Modal.getInstance($("expense-edit-modal")).hide();
      showSuccess("支出已更新");
      await refreshAll();
    } catch (error) {
      showError(error.detail);
    }
  });

  async function deleteExpense(expenseId) {
    if (!window.confirm("确定删除这笔支出吗？删除后余额会自动恢复。")) {
      return;
    }
    try {
      await API.delete("/api/expenses/" + expenseId + "/");
      showSuccess("支出已删除");
      await refreshAll();
    } catch (error) {
      showError(error.detail);
    }
  }

  /* ---------------- 余额 ---------------- */
  function renderBalances() {
    var data = state.balances;
    if (!data) {
      return;
    }

    var box = $("my-balance");
    var mine = Number(data.my_net_balance);
    if (mine > 0) {
      box.className = "alert alert-success";
      box.textContent = "应收 " + API.money(mine) + " 元";
    } else if (mine < 0) {
      box.className = "alert alert-danger";
      box.textContent = "应付 " + API.money(Math.abs(mine)) + " 元";
    } else {
      box.className = "alert alert-secondary";
      box.textContent = "已结清";
    }

    var balanceRows = $("balance-rows");
    balanceRows.innerHTML = "";
    (data.balances || []).forEach(function (item) {
      var row = document.createElement("tr");
      row.innerHTML =
        "<td>" +
        escapeHtml(item.nickname || item.username) +
        "</td>" +
        "<td>" +
        escapeHtml(item.role === "admin" ? "管理员" : "成员") +
        "</td>" +
        '<td class="text-end">' +
        API.money(item.net_balance) +
        "</td>";
      balanceRows.appendChild(row);
    });

    var relationshipRows = $("relationship-rows");
    relationshipRows.innerHTML = "";
    var relationships = data.relationships || [];
    relationships.forEach(function (item) {
      var row = document.createElement("tr");
      row.innerHTML =
        "<td>" +
        escapeHtml(item.nickname || item.username) +
        "</td>" +
        '<td class="text-end">' +
        API.money(item.owes_me) +
        "</td>" +
        '<td class="text-end">' +
        API.money(item.i_owe) +
        "</td>";
      relationshipRows.appendChild(row);
    });
    $("relationship-empty").classList.toggle("d-none", relationships.length > 0);
  }

  /* ---------------- 结算 ---------------- */
  function renderSuggestions() {
    var list = $("suggestion-list");
    list.innerHTML = "";

    var settled =
      state.transfers.length === 0 && state.balances && state.balances.is_settled;
    $("settle-alert").classList.toggle("d-none", !settled);

    state.transfers.forEach(function (transfer) {
      var column = document.createElement("div");
      column.className = "col-md-6";
      column.innerHTML =
        '<div class="card border-primary h-100"><div class="card-body d-flex justify-content-between align-items-center">' +
        "<div><strong>" +
        escapeHtml(transfer.from_username) +
        " → " +
        escapeHtml(transfer.to_username) +
        "</strong>" +
        '<div class="text-muted small">' +
        API.money(transfer.amount) +
        " 元</div></div>" +
        '<button class="btn btn-sm btn-success" data-from="' +
        transfer.from_user_id +
        '" data-to="' +
        transfer.to_user_id +
        '" data-amount="' +
        transfer.amount +
        '">已线下转账</button>' +
        "</div></div>";
      list.appendChild(column);
    });

    Array.prototype.slice
      .call(list.querySelectorAll("button[data-from]"))
      .forEach(function (button) {
        button.addEventListener("click", function () {
          recordSettlement(button.dataset);
        });
      });
  }

  async function recordSettlement(dataset) {
    try {
      await API.post("/api/groups/" + GROUP_ID + "/settlements/", {
        from_user_id: Number(dataset.from),
        to_user_id: Number(dataset.to),
        amount: dataset.amount,
      });
      showSuccess("结算已登记");
      await refreshAll();
    } catch (error) {
      showError(error.detail);
    }
  }

  function renderSettlements(settlements) {
    var tbody = $("settlement-rows");
    tbody.innerHTML = "";
    $("settlement-empty").classList.toggle("d-none", settlements.length > 0);

    settlements.forEach(function (item) {
      var row = document.createElement("tr");
      row.innerHTML =
        "<td>" +
        escapeHtml(item.from_user.username) +
        " → " +
        escapeHtml(item.to_user.username) +
        "</td>" +
        '<td class="text-end">' +
        API.money(item.amount) +
        "</td>" +
        "<td>" +
        escapeHtml((item.created_at || "").replace("T", " ").slice(0, 19)) +
        "</td>";
      tbody.appendChild(row);
    });
  }

  $("load-suggestions").addEventListener("click", async function () {
    try {
      await loadSuggestions();
      showSuccess("结算建议已刷新");
    } catch (error) {
      showError(error.detail);
    }
  });

  /* ---------------- 退出群组 ---------------- */
  $("leave-group").addEventListener("click", async function () {
    if (!window.confirm("确定退出该群组吗？余额不为 0 时无法退出。")) {
      return;
    }
    try {
      await API.post("/api/groups/" + GROUP_ID + "/leave/");
      showSuccess("已退出群组");
      window.location.href = "/groups/";
    } catch (error) {
      showError(error.detail);
    }
  });

  /* ---------------- AI 问答 ---------------- */
  function renderAnswer(data) {
    $("ai-answer").textContent = data.answer || "";
    $("ai-answer-card").classList.remove("d-none");

    var tbody = $("ai-sources");
    tbody.innerHTML = "";
    var sources = data.sources || [];
    sources.forEach(function (item) {
      var row = document.createElement("tr");
      row.innerHTML =
        "<td>" +
        escapeHtml((item.created_at || "").slice(0, 10)) +
        "</td>" +
        "<td>" +
        escapeHtml(item.payer_username) +
        "</td>" +
        "<td>" +
        escapeHtml(item.note || "-") +
        "</td>" +
        '<td class="text-end">' +
        API.money(item.amount) +
        "</td>";
      tbody.appendChild(row);
    });
    $("ai-sources-empty").classList.toggle("d-none", sources.length > 0);
  }

  async function askAi() {
    var question = $("ai-question").value.trim();
    if (!question) {
      showError("请先输入问题");
      return;
    }
    $("ai-ask").disabled = true;
    try {
      var data = await API.post("/api/groups/" + GROUP_ID + "/query/", {
        question: question,
      });
      $("ai-query-disabled").classList.add("d-none");
      renderAnswer(data);
    } catch (error) {
      if (error.detail && error.detail.indexOf("未启用") >= 0) {
        $("ai-query-disabled").classList.remove("d-none");
      }
      showError(error.detail);
    } finally {
      $("ai-ask").disabled = false;
    }
  }

  $("ai-ask").addEventListener("click", askAi);
  $("ai-question").addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      askAi();
    }
  });

  /* ---------------- 初始化 ---------------- */
  (async function init() {
    try {
      await loadGroup();
      await loadBalances();
      await loadExpenses();
      await loadSuggestions();
      await loadSettlements();
      renderSplitInputs();
    } catch (error) {
      showError(error.detail);
    }
  })();
})();
