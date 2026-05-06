const titleByView = {
  dashboard: "今日看板",
  inbox: "收件箱",
  tasks: "TODO 中心",
  memos: "备忘录",
  knowledge: "知识库",
  graph: "知识图谱",
  settings: "设置中心",
};

const state = {
  view: "dashboard",
  data: null,
  selectedNoteId: "",
  notificationEnabled: localStorage.getItem("aylaNotificationsEnabled") === "true",
  inboxFilters: {
    status: "all",
    type: "all",
  },
  taskFilter: "active",
};

const root = document.querySelector("#view-root");
const titleEl = document.querySelector("#view-title");
const statusEl = document.querySelector("#workspace-status");
const toastEl = document.querySelector("#toast");
const notifyButton = document.querySelector('[data-action="toggle-notifications"]');

function todayDateKey() {
  return new Date().toISOString().slice(0, 10);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "未设置";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateOnly(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  window.setTimeout(() => toastEl.classList.remove("show"), 2200);
}

function notificationSupported() {
  return "Notification" in window;
}

function updateNotificationButton() {
  if (!notifyButton) return;
  if (!notificationSupported()) {
    notifyButton.textContent = "提醒不可用";
    notifyButton.disabled = true;
    return;
  }
  if (state.notificationEnabled && Notification.permission === "granted") {
    notifyButton.textContent = "提醒开启";
    notifyButton.classList.add("active");
  } else if (Notification.permission === "denied") {
    notifyButton.textContent = "提醒被拒";
    notifyButton.classList.remove("active");
  } else {
    notifyButton.textContent = "开启提醒";
    notifyButton.classList.remove("active");
  }
}

function notifiedKeys() {
  try {
    return JSON.parse(localStorage.getItem("aylaNotifiedTasks") || "{}");
  } catch {
    return {};
  }
}

function saveNotifiedKeys(keys) {
  localStorage.setItem("aylaNotifiedTasks", JSON.stringify(keys));
}

function checkTaskNotifications() {
  if (!state.data || !state.notificationEnabled || !notificationSupported() || Notification.permission !== "granted") {
    return;
  }
  const today = todayDateKey();
  const keys = notifiedKeys();
  for (const task of state.data.tasks || []) {
    if (!task.due_at || ["已完成", "已取消", "已归档"].includes(task.status)) continue;
    if (task.due_at > today) continue;
    const key = `${today}:${task.id}:${task.due_at}:${task.status}`;
    if (keys[key]) continue;
    const overdue = task.due_at < today ? "已逾期" : "今天截止";
    try {
      new Notification("Ayla TODO 提醒", {
        body: `${overdue}：${task.title}`,
        tag: key,
      });
    } catch {
      return;
    }
    keys[key] = Date.now();
  }
  saveNotifiedKeys(keys);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

async function loadState() {
  state.data = await api("/api/state");
  statusEl.textContent = state.data.workspace;
  if (!state.selectedNoteId && state.data.notes.length) {
    state.selectedNoteId = state.data.notes[0].id;
  }
  updateNotificationButton();
  checkTaskNotifications();
}

function setView(view) {
  state.view = view;
  window.location.hash = view;
  render();
}

function metaPill(label, tone = "") {
  return `<span class="pill ${tone}">${escapeHtml(label)}</span>`;
}

function statusTone(status) {
  if (["待确认", "候选", "待办", "进行中", "自动分类"].includes(status)) return "green";
  if (["需补充"].includes(status)) return "amber";
  if (["已忽略", "已取消"].includes(status)) return "coral";
  if (["已确认", "已完成", "已归档", "已发布"].includes(status)) return "violet";
  return "";
}

function typeLabel(type) {
  const labels = {
    memo: "备忘",
    task_candidate: "TODO 候选",
    summary: "摘要",
    note_candidate: "笔记候选",
    pinned_candidate: "固定便笺候选",
  };
  return labels[type] || type;
}

function targetLabel(target) {
  const labels = {
    todo: "自动归为 TODO",
    note: "自动归为知识",
    pinned: "自动归为便笺",
    memo: "仅归档备忘",
  };
  return labels[target] || target;
}

function confidence(item) {
  return `${Math.round((Number(item.confidence) || 0) * 100)}%`;
}

function sourceLink(url) {
  if (!url) return "";
  return `<a class="source-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">来源链接</a>`;
}

function renderQuickMemo(variant = "default") {
  return `
    <form class="memo-form" data-form="memo">
      <div class="memo-mode" role="radiogroup" aria-label="记录模式">
        <label>
          <input type="radio" name="mode" value="auto" checked />
          <span>自动整理</span>
        </label>
        <label>
          <input type="radio" name="mode" value="pinned" />
          <span>固定便笺</span>
        </label>
      </div>
      <div class="form-row">
        <label for="memo-content-${variant}">备忘内容</label>
        <textarea class="textarea" id="memo-content-${variant}" name="content" placeholder="写下刚刚发生的事、需要跟进的任务或值得沉淀的资料。默认进入今日整理；切到固定便笺时会直接新增一张长期便笺。"></textarea>
      </div>
      <div class="form-row pinned-memo-fields" hidden>
        <label for="memo-title-${variant}">便笺标题</label>
        <input class="input" id="memo-title-${variant}" name="title" placeholder="不填则用第一行内容作为标题" />
      </div>
      <div class="form-row">
        <label for="memo-partition-${variant}">分区</label>
        <select class="select" id="memo-partition-${variant}" name="partition">
          <option value="">自动判断</option>
          <option value="工作">工作</option>
          <option value="学习">学习</option>
          <option value="项目">项目</option>
          <option value="个人">个人</option>
          <option value="待整理">待整理</option>
        </select>
      </div>
      <div class="form-actions">
        <button class="button" type="submit">记录</button>
      </div>
    </form>
  `;
}

function setMemoMode(form, mode) {
  const isPinned = mode === "pinned";
  const titleField = form.querySelector(".pinned-memo-fields");
  const submit = form.querySelector('button[type="submit"]');
  if (titleField) {
    titleField.hidden = !isPinned;
  }
  if (submit) {
    submit.textContent = isPinned ? "新增固定便笺" : "记录";
  }
}

function renderStats() {
  const stats = state.data.stats;
  return `
    <div class="stats-grid">
      <div class="stat">
        <strong>${stats.pending_inbox}</strong>
        <span>待整理</span>
      </div>
      <div class="stat">
        <strong>${stats.today_tasks}</strong>
        <span>未完成 TODO</span>
      </div>
      <div class="stat">
        <strong>${stats.notes}</strong>
        <span>知识笔记</span>
      </div>
      <div class="stat">
        <strong>${stats.risks}</strong>
        <span>风险提示</span>
      </div>
    </div>
  `;
}

function renderDailyReview(compact = false) {
  const review = state.data.daily_review || { today: "", items: [], today_count: 0, pending_count: 0 };
  const items = review.items || [];
  const rows = compact ? items.slice(0, 3) : items;
  return `
    <section class="panel">
      <div class="section-head">
        <div>
          <h2>今日增量整理</h2>
          <p>${escapeHtml(review.today)}，${review.today_count || 0} 条今日自动分类，${review.pending_count || 0} 条待整理总量</p>
        </div>
        <button class="button" data-action="confirm-daily-review" data-date="${escapeHtml(review.today)}" ${items.length ? "" : "disabled"} type="button">确认今日整理</button>
      </div>
      <div class="list">
        ${rows.length ? rows.map((item) => renderReviewItem(item, compact)).join("") : `<div class="empty">今天还没有待整理增量</div>`}
      </div>
    </section>
  `;
}

function renderReviewItem(item, compact = false) {
  const metadata = item.metadata || {};
  if (compact) {
    return `
      <article class="item-row compact">
        <div class="meta-line">
          ${metaPill(targetLabel(item.auto_target), "green")}
          ${metaPill(item.suggested_category || "待整理")}
          ${metadata.parser_status === "agent" ? metaPill("OpenClaw", "violet") : ""}
          ${metadata.parser_status === "parsed" ? metaPill("已解析链接", "violet") : ""}
          ${metadata.parser_status === "failed" ? metaPill("链接待解析", "amber") : ""}
          ${metadata.project ? metaPill(metadata.project, "violet") : ""}
          <span>置信度 ${confidence(item)}</span>
          ${sourceLink(metadata.source_url)}
        </div>
        <h3>${escapeHtml(item.title)}</h3>
        <p class="content-preview">${escapeHtml(item.content)}</p>
      </article>
    `;
  }
  const tags = Array.isArray(metadata.tags) ? metadata.tags.join(", ") : "";
  return `
    <form class="review-card" data-review-row="${escapeHtml(item.id)}">
      <div class="review-grid">
        <div class="form-row">
          <label>整理到</label>
          <select class="select" name="target">
            ${["todo", "note", "pinned", "memo"].map((target) => `<option value="${target}" ${target === item.auto_target ? "selected" : ""}>${targetLabel(target)}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>分类</label>
          <select class="select" name="category">
            ${["工作", "学习", "项目", "个人", "待整理", "可公开"].map((category) => `<option value="${category}" ${category === item.suggested_category ? "selected" : ""}>${category}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>优先级</label>
          <select class="select" name="priority">
            ${["high", "medium", "normal", "low"].map((priority) => `<option value="${priority}" ${priority === (metadata.suggested_priority || "normal") ? "selected" : ""}>${priority}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>截止时间</label>
          <input class="input" name="due_at" type="date" value="${escapeHtml(metadata.suggested_due_at || "")}" />
        </div>
      </div>
      <div class="form-row">
        <label>标题</label>
        <input class="input" name="title" value="${escapeHtml(item.title)}" />
      </div>
      <div class="form-row">
        <label>内容</label>
        <textarea class="textarea review-textarea" name="content">${escapeHtml(item.content)}</textarea>
      </div>
      <div class="review-grid secondary">
        <div class="form-row">
          <label>项目</label>
          <input class="input" name="project_id" value="${escapeHtml(metadata.project || "")}" />
        </div>
        <div class="form-row">
          <label>标签</label>
          <input class="input" name="tags" value="${escapeHtml(tags)}" placeholder="逗号分隔" />
        </div>
      </div>
      <div class="meta-line">
        <span>置信度 ${confidence(item)}</span>
        ${metadata.parser_status === "agent" ? metaPill("OpenClaw", "violet") : ""}
        ${metadata.parser_status === "parsed" ? metaPill("已解析链接", "violet") : ""}
        ${metadata.parser_status === "failed" ? metaPill("链接待解析", "amber") : ""}
        ${sourceLink(metadata.source_url)}
        <span>${formatDate(item.created_at)}</span>
      </div>
    </form>
  `;
}

function collectDailyReviewOverrides() {
  return [...document.querySelectorAll("[data-review-row]")].map((form) => ({
    id: form.dataset.reviewRow,
    ...Object.fromEntries(new FormData(form).entries()),
  }));
}

function renderPinnedSlots() {
  const slots = state.data.pinned_slots || [];
  return `
    <section class="panel notes-panel">
      <div class="section-head">
        <div>
          <h2>固定便笺</h2>
          <p>长期固定的信息槽位。也可以在“记录备忘”里切到固定便笺来新增。</p>
        </div>
        <button class="button secondary" data-action="add-pinned-slot" type="button">新建便笺</button>
      </div>
      <div class="pinned-grid">
        ${slots.map((slot) => `
          <form class="slot-card apple-note" data-form="pinned-slot" data-id="${escapeHtml(slot.id)}">
            <div class="slot-card-head">
              <div>
                <input class="slot-title" name="title" value="${escapeHtml(slot.title)}" aria-label="便笺标题" />
                <div class="slot-date">${formatDate(slot.updated_at)}</div>
              </div>
              <select class="slot-category" name="category" aria-label="槽位分区">
                ${["工作", "学习", "项目", "个人", "待整理"].map((item) => `<option value="${item}" ${item === slot.category ? "selected" : ""}>${item}</option>`).join("")}
              </select>
            </div>
            <textarea class="slot-textarea" name="content" aria-label="${escapeHtml(slot.title)}内容">${escapeHtml(slot.content)}</textarea>
            <div class="note-card-actions">
              <button class="button secondary" type="submit">保存</button>
              <button class="button warning" data-action="delete-pinned-slot" data-id="${escapeHtml(slot.id)}" type="button">删除</button>
            </div>
          </form>
        `).join("")}
      </div>
    </section>
  `;
}

function renderInboxItem(item) {
  const metadata = item.metadata || {};
  const project = metadata.project ? metaPill(metadata.project, "violet") : "";
  const risk = metadata.risk ? metaPill("风险", "coral") : "";
  const due = metadata.suggested_due_at ? metaPill(`截止 ${formatDateOnly(metadata.suggested_due_at)}`, "amber") : "";
  const target = metadata.auto_target ? metaPill(targetLabel(metadata.auto_target), "green") : "";
  const parser = metadata.parser_status === "agent" ? metaPill("OpenClaw", "violet") : metadata.parser_status === "parsed" ? metaPill("已解析链接", "violet") : metadata.parser_status === "failed" ? metaPill("链接待解析", "amber") : "";
  const canAct = !["已确认", "已忽略", "已归档", "已发布"].includes(item.status);
  return `
    <article class="item-row">
      <div class="meta-line">
        ${metaPill(typeLabel(item.item_type), "green")}
        ${metaPill(item.status, statusTone(item.status))}
        ${metaPill(item.suggested_category || "待整理")}
        ${target}
        ${project}
        ${risk}
        ${due}
        ${parser}
        <span>置信度 ${confidence(item)}</span>
        ${sourceLink(metadata.source_url)}
        <span>${formatDate(item.updated_at)}</span>
      </div>
      <div>
        <h3>${escapeHtml(item.title)}</h3>
        <p class="content-preview">${escapeHtml(item.content)}</p>
      </div>
      <div class="row-actions">
        <button class="button" data-action="confirm-task" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">转 TODO</button>
        <button class="button secondary" data-action="confirm-note" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">入知识库</button>
        <button class="button secondary" data-action="need-info" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">需补充</button>
        <button class="button warning" data-action="ignore" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">忽略</button>
      </div>
    </article>
  `;
}

function renderTask(task) {
  return `
    <article class="task-row" data-task-row="${escapeHtml(task.id)}">
      <div class="meta-line">
        ${metaPill(task.status, statusTone(task.status))}
        ${metaPill(task.priority || "normal")}
        ${task.due_at ? metaPill(`截止 ${formatDateOnly(task.due_at)}`, "amber") : ""}
        ${task.project_id ? metaPill(task.project_id, "violet") : ""}
        <span>来源 ${escapeHtml(task.source_title || "本地")}</span>
      </div>
      <div class="task-editor">
        <div class="form-row">
          <label>标题</label>
          <input class="input" name="title" value="${escapeHtml(task.title)}" />
        </div>
        <div class="form-row">
          <label>状态</label>
          <select class="select" name="status">
            ${["待办", "进行中", "已完成", "已取消", "已归档"].map((item) => `<option value="${item}" ${item === task.status ? "selected" : ""}>${item}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>优先级</label>
          <select class="select" name="priority">
            ${["high", "medium", "normal", "low"].map((item) => `<option value="${item}" ${item === task.priority ? "selected" : ""}>${item}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>截止时间</label>
          <input class="input" name="due_at" type="date" value="${escapeHtml(task.due_at || "")}" />
        </div>
        <div class="form-row">
          <label>项目</label>
          <input class="input" name="project_id" value="${escapeHtml(task.project_id || "")}" />
        </div>
        <button class="button secondary" data-action="save-task" data-id="${escapeHtml(task.id)}" type="button">保存</button>
      </div>
      <p class="content-preview">${escapeHtml(task.description || "")}</p>
    </article>
  `;
}

function renderDashboard() {
  const pending = state.data.inbox.filter((item) => ["待确认", "未处理", "需补充", "自动分类"].includes(item.status)).slice(0, 5);
  const activeTasks = state.data.tasks.filter((task) => !["已完成", "已取消", "已归档"].includes(task.status)).slice(0, 5);
  const recentNotes = state.data.notes.slice(0, 5);

  root.innerHTML = `
    <div class="stack">
      ${renderStats()}
      <div class="dashboard-grid">
        <div class="stack">
          <section class="panel">
            <h2>快速备忘</h2>
            ${renderQuickMemo("dashboard")}
          </section>
          ${renderDailyReview(true)}
          <section class="panel">
            <h2>待确认</h2>
            <div class="list">
              ${pending.length ? pending.map(renderInboxItem).join("") : `<div class="empty">暂无待确认内容</div>`}
            </div>
          </section>
        </div>
        <div class="stack">
          <section class="panel">
            <h2>今日 TODO</h2>
            <div class="list">
              ${activeTasks.length ? activeTasks.map(renderTask).join("") : `<div class="empty">暂无未完成 TODO</div>`}
            </div>
          </section>
          <section class="panel">
            <h2>最近知识</h2>
            <div class="note-list">
              ${recentNotes.length ? recentNotes.map(renderNoteButton).join("") : `<div class="empty">暂无知识笔记</div>`}
            </div>
          </section>
        </div>
      </div>
    </div>
  `;
}

function renderInbox() {
  const filtered = state.data.inbox.filter((item) => {
    const statusOk = state.inboxFilters.status === "all" || item.status === state.inboxFilters.status;
    const typeOk = state.inboxFilters.type === "all" || item.item_type === state.inboxFilters.type;
    return statusOk && typeOk;
  });
  root.innerHTML = `
    <div class="panel">
      <div class="toolbar">
        <select class="select" data-filter="inbox-status">
          ${["all", "自动分类", "待确认", "需补充", "已确认", "已忽略", "已归档"].map((item) => `<option value="${item}" ${item === state.inboxFilters.status ? "selected" : ""}>${item === "all" ? "全部状态" : item}</option>`).join("")}
        </select>
        <select class="select" data-filter="inbox-type">
          ${["all", "memo", "task_candidate", "summary", "note_candidate", "pinned_candidate"].map((item) => `<option value="${item}" ${item === state.inboxFilters.type ? "selected" : ""}>${item === "all" ? "全部类型" : typeLabel(item)}</option>`).join("")}
        </select>
      </div>
      <div class="list">
        ${filtered.length ? filtered.map(renderInboxItem).join("") : `<div class="empty">当前筛选下没有内容</div>`}
      </div>
    </div>
  `;
}

function renderTasks() {
  const tasks = state.data.tasks.filter((task) => {
    if (state.taskFilter === "done") return task.status === "已完成";
    if (state.taskFilter === "active") return !["已完成", "已取消", "已归档"].includes(task.status);
    return true;
  });
  root.innerHTML = `
    <div class="panel">
      <div class="toolbar">
        <select class="select" data-filter="tasks">
          <option value="active" ${state.taskFilter === "active" ? "selected" : ""}>未完成</option>
          <option value="done" ${state.taskFilter === "done" ? "selected" : ""}>已完成</option>
          <option value="all" ${state.taskFilter === "all" ? "selected" : ""}>全部</option>
        </select>
      </div>
      <div class="list">
        ${tasks.length ? tasks.map(renderTask).join("") : `<div class="empty">暂无 TODO</div>`}
      </div>
    </div>
  `;
}

function renderMemos() {
  const memoEvents = state.data.events.filter((event) => event.source_type === "manual_memo").slice(0, 16);
  root.innerHTML = `
    <div class="stack">
      ${renderPinnedSlots()}
      ${renderDailyReview(false)}
    </div>
    <div class="dashboard-grid memo-lower">
      <section class="panel">
        <h2>新备忘</h2>
        ${renderQuickMemo("memos")}
      </section>
      <section class="panel">
        <h2>最近备忘</h2>
        <div class="list">
          ${memoEvents.length ? memoEvents.map((event) => `
            <article class="item-row">
              <div class="meta-line">
                ${metaPill("手动输入", "green")}
                <span>${formatDate(event.collected_at)}</span>
              </div>
              <h3>${escapeHtml(event.title || "备忘")}</h3>
              <p class="content-preview">${escapeHtml(event.content)}</p>
            </article>
          `).join("") : `<div class="empty">暂无备忘</div>`}
        </div>
      </section>
    </div>
    <section class="panel" style="margin-top:16px">
      <h2>模拟飞书摘要导入</h2>
      <form class="memo-form" data-form="summary">
        <div class="form-row">
          <label for="summary-title">标题</label>
          <input class="input" id="summary-title" name="title" value="昨日关注群摘要" />
        </div>
        <div class="form-row">
          <label for="summary-content">摘要内容</label>
          <textarea class="textarea" id="summary-content" name="content" placeholder="粘贴群聊摘要、会议纪要或长文本"></textarea>
        </div>
        <div class="form-actions">
          <button class="button" type="submit">导入摘要</button>
        </div>
      </form>
    </section>
  `;
}

function renderNoteButton(note) {
  const active = note.id === state.selectedNoteId ? "active" : "";
  const tags = Array.isArray(note.tags) ? note.tags.slice(0, 3) : [];
  return `
    <button class="note-button ${active}" data-action="select-note" data-id="${escapeHtml(note.id)}" type="button">
      <strong>${escapeHtml(note.title)}</strong>
      <span class="meta-line">
        ${metaPill(note.type || "笔记", "green")}
        ${tags.map((tag) => metaPill(`#${tag}`)).join("")}
      </span>
    </button>
  `;
}

function renderKnowledge() {
  const notes = state.data.notes;
  const selected = notes.find((note) => note.id === state.selectedNoteId) || notes[0];
  if (selected && selected.id !== state.selectedNoteId) {
    state.selectedNoteId = selected.id;
  }
  root.innerHTML = `
    <div class="split-view">
      <section class="panel">
        <h2>笔记列表</h2>
        <div class="note-list">
          ${notes.length ? notes.map(renderNoteButton).join("") : `<div class="empty">暂无知识笔记</div>`}
        </div>
      </section>
      <section class="note-detail">
        ${selected ? `
          <div class="meta-line">
            ${metaPill(selected.type || "笔记", "green")}
            ${selected.sensitivity ? metaPill(selected.sensitivity, selected.sensitivity === "private" ? "amber" : "violet") : ""}
            ${selected.publishable ? metaPill("可发布", "violet") : ""}
          </div>
          <h2>${escapeHtml(selected.title)}</h2>
          <div class="note-path">${escapeHtml(selected.path)}</div>
          <div class="row-actions" style="margin-bottom:12px">
            <button class="button secondary" data-action="copy-note-path" data-id="${escapeHtml(selected.id)}" type="button">复制路径</button>
            <button class="button secondary" data-action="copy-note-markdown" data-id="${escapeHtml(selected.id)}" type="button">复制 Markdown</button>
            <button class="button warning" data-action="delete-note" data-id="${escapeHtml(selected.id)}" type="button">删除知识</button>
          </div>
          <pre class="markdown-preview">${escapeHtml(selected.content)}</pre>
        ` : `<div class="empty">暂无可查看内容</div>`}
      </section>
    </div>
  `;
}

function graphData() {
  const nodes = [];
  const nodeMap = new Map();
  const links = [];
  const addNode = (id, label, type) => {
    if (!id || nodeMap.has(id)) return;
    nodeMap.set(id, { id, label, type });
    nodes.push(nodeMap.get(id));
  };
  const addLink = (source, target) => {
    if (!source || !target || source === target) return;
    links.push({ source, target });
  };
  for (const note of state.data.notes) {
    addNode(note.id, note.title, "note");
    const projects = Array.isArray(note.projects) ? note.projects : [];
    for (const project of projects) {
      const projectId = `project:${project}`;
      addNode(projectId, project, "project");
      addLink(note.id, projectId);
    }
    const tags = Array.isArray(note.tags) ? note.tags : [];
    for (const tag of tags.slice(0, 2)) {
      const tagId = `tag:${tag}`;
      addNode(tagId, `#${tag}`, "tag");
      addLink(note.id, tagId);
    }
  }
  return { nodes: nodes.slice(0, 28), links: links.slice(0, 42) };
}

function renderGraph() {
  const graph = graphData();
  if (!graph.nodes.length) {
    root.innerHTML = `<div class="empty">暂无图谱节点</div>`;
    return;
  }
  const width = 980;
  const height = 560;
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.36;
  const positions = new Map();
  graph.nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / graph.nodes.length - Math.PI / 2;
    positions.set(node.id, {
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    });
  });
  const color = (type) => {
    if (type === "project") return "#1f7a6b";
    if (type === "task") return "#c85b45";
    if (type === "tag") return "#9f7417";
    return "#5b5f97";
  };
  root.innerHTML = `
    <div class="graph-canvas">
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="知识图谱">
        ${graph.links.map((link) => {
          const a = positions.get(link.source);
          const b = positions.get(link.target);
          if (!a || !b) return "";
          return `<line class="graph-line" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" />`;
        }).join("")}
        ${graph.nodes.map((node) => {
          const point = positions.get(node.id);
          const label = node.label.length > 12 ? `${node.label.slice(0, 11)}…` : node.label;
          return `
            <g class="graph-node" transform="translate(${point.x}, ${point.y})">
              <circle r="24" fill="${color(node.type)}"></circle>
              <text y="42">${escapeHtml(label)}</text>
            </g>
          `;
        }).join("")}
      </svg>
    </div>
  `;
}

function renderSettings() {
  const settings = state.data.settings;
  root.innerHTML = `
    <form class="settings-panel" data-form="settings">
      <div class="settings-grid">
        <div class="form-row">
          <label for="vault-path">Obsidian Vault 路径</label>
          <input class="input" id="vault-path" name="vault_path" value="${escapeHtml(settings.vault_path || "")}" />
        </div>
        <div class="form-row">
          <label for="summary-frequency">摘要频率</label>
          <select class="select" id="summary-frequency" name="summary_frequency">
            ${["manual", "daily", "twice_daily"].map((item) => `<option value="${item}" ${item === settings.summary_frequency ? "selected" : ""}>${item}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label for="model-provider">模型配置</label>
          <input class="input" id="model-provider" name="model_provider" value="${escapeHtml(settings.model_provider || "")}" />
        </div>
        <div class="form-row">
          <label for="github-repo">GitHub 仓库</label>
          <input class="input" id="github-repo" name="github_repo" value="${escapeHtml(settings.github_repo || "")}" />
        </div>
        <div class="form-row token-row">
          <label for="agent-token">OpenClaw 写入 Token</label>
          <input class="input token-input" id="agent-token" readonly value="${escapeHtml(settings.agent_api_token || "")}" />
        </div>
      </div>
      <div class="form-actions" style="margin-top:16px">
        <button class="button" type="submit">保存设置</button>
        <button class="button secondary" data-action="copy-agent-token" type="button">复制 Token</button>
        <button class="button warning" data-action="rotate-agent-token" type="button">重置 Token</button>
      </div>
    </form>
    <section class="panel" style="margin-top:16px">
      <h2>审计日志</h2>
      <div class="list">
        ${state.data.audit_logs.length ? state.data.audit_logs.map((log) => `
          <article class="item-row">
            <div class="meta-line">
              ${metaPill(log.action, "green")}
              <span>${formatDate(log.created_at)}</span>
            </div>
            <p>${escapeHtml(log.target_type || "")} ${escapeHtml(log.target_id || "")}</p>
          </article>
        `).join("") : `<div class="empty">暂无审计日志</div>`}
      </div>
    </section>
  `;
}

function render() {
  if (!state.data) return;
  titleEl.textContent = titleByView[state.view] || "个人 Agent 工作台";
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.view);
  });
  if (state.view === "dashboard") renderDashboard();
  if (state.view === "inbox") renderInbox();
  if (state.view === "tasks") renderTasks();
  if (state.view === "memos") renderMemos();
  if (state.view === "knowledge") renderKnowledge();
  if (state.view === "graph") renderGraph();
  if (state.view === "settings") renderSettings();
}

async function refresh(message = "") {
  await loadState();
  render();
  if (message) showToast(message);
}

document.addEventListener("click", async (event) => {
  const navButton = event.target.closest("[data-view]");
  if (navButton) {
    setView(navButton.dataset.view);
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  const action = actionButton.dataset.action;
  const id = actionButton.dataset.id;

  try {
    if (action === "refresh") {
      await refresh("已刷新");
      return;
    }
    if (action === "toggle-notifications") {
      if (!notificationSupported()) {
        showToast("当前浏览器不支持通知");
        return;
      }
      if (Notification.permission !== "granted") {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          state.notificationEnabled = false;
          localStorage.setItem("aylaNotificationsEnabled", "false");
          updateNotificationButton();
          showToast("未开启通知权限");
          return;
        }
      }
      state.notificationEnabled = !state.notificationEnabled;
      localStorage.setItem("aylaNotificationsEnabled", String(state.notificationEnabled));
      updateNotificationButton();
      checkTaskNotifications();
      showToast(state.notificationEnabled ? "TODO 提醒已开启" : "TODO 提醒已关闭");
      return;
    }
    if (action === "copy-agent-token") {
      await navigator.clipboard.writeText(state.data.settings.agent_api_token || "");
      showToast("Agent Token 已复制");
      return;
    }
    if (action === "rotate-agent-token") {
      const ok = window.confirm("重置 OpenClaw 写入 Token？旧 Token 会立即失效。");
      if (!ok) return;
      await api("/api/agent/token/rotate", {
        method: "POST",
        headers: {
          "X-Ayla-Agent-Token": state.data.settings.agent_api_token || "",
        },
        body: "{}",
      });
      await refresh("Agent Token 已重置");
      return;
    }
    if (action === "confirm-daily-review") {
      await api("/api/daily-review/confirm", {
        method: "POST",
        body: JSON.stringify({
          date: actionButton.dataset.date,
          overrides: collectDailyReviewOverrides(),
        }),
      });
      await refresh("今日增量已整理");
      return;
    }
    if (action === "add-pinned-slot") {
      await api("/api/pinned-slots", {
        method: "POST",
        body: JSON.stringify({ title: "新的固定便笺", category: "待整理", content: "" }),
      });
      await refresh("已新增固定便笺");
      return;
    }
    if (action === "delete-pinned-slot") {
      await api(`/api/pinned-slots/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refresh("固定便笺已删除");
      return;
    }
    if (action === "confirm-task") {
      await api(`/api/inbox/${encodeURIComponent(id)}/confirm-task`, { method: "POST", body: "{}" });
      await refresh("已转为 TODO");
      return;
    }
    if (action === "confirm-note") {
      await api(`/api/inbox/${encodeURIComponent(id)}/confirm-note`, { method: "POST", body: "{}" });
      await refresh("已写入知识库");
      return;
    }
    if (action === "need-info") {
      await api(`/api/inbox/${encodeURIComponent(id)}/need-info`, { method: "POST", body: "{}" });
      await refresh("已标记需补充");
      return;
    }
    if (action === "ignore") {
      await api(`/api/inbox/${encodeURIComponent(id)}/ignore`, { method: "POST", body: "{}" });
      await refresh("已忽略");
      return;
    }
    if (action === "save-task") {
      const row = document.querySelector(`[data-task-row="${CSS.escape(id)}"]`);
      const payload = {};
      row.querySelectorAll("input[name], select[name], textarea[name]").forEach((input) => {
        payload[input.name] = input.value;
      });
      await api(`/api/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      await refresh("TODO 已保存");
      return;
    }
    if (action === "select-note") {
      state.selectedNoteId = id;
      render();
      return;
    }
    if (action === "copy-note-path") {
      const note = state.data.notes.find((item) => item.id === id);
      await navigator.clipboard.writeText(note?.path || "");
      showToast("路径已复制");
      return;
    }
    if (action === "copy-note-markdown") {
      const note = state.data.notes.find((item) => item.id === id);
      await navigator.clipboard.writeText(note?.content || "");
      showToast("Markdown 已复制");
      return;
    }
    if (action === "delete-note") {
      const note = state.data.notes.find((item) => item.id === id);
      const ok = window.confirm(`删除知识笔记「${note?.title || ""}」？本地 Markdown 文件也会一起删除。`);
      if (!ok) return;
      await api(`/api/notes/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.selectedNoteId = "";
      await refresh("知识笔记已删除");
      return;
    }
  } catch (error) {
    showToast(error.message);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.name === "mode") {
    const form = event.target.closest('[data-form="memo"]');
    if (form) {
      setMemoMode(form, event.target.value);
    }
    return;
  }
  const filter = event.target.dataset.filter;
  if (!filter) return;
  if (filter === "inbox-status") {
    state.inboxFilters.status = event.target.value;
    render();
  }
  if (filter === "inbox-type") {
    state.inboxFilters.type = event.target.value;
    render();
  }
  if (filter === "tasks") {
    state.taskFilter = event.target.value;
    render();
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  const kind = form.dataset.form;
  if (!kind) return;
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form).entries());

  try {
    if (kind === "memo") {
      if (!String(data.content || "").trim()) {
        showToast("请输入备忘内容");
        return;
      }
      if (data.mode === "pinned") {
        await api("/api/pinned-slots", {
          method: "POST",
          body: JSON.stringify({
            title: data.title,
            content: data.content,
            category: data.partition || "待整理",
          }),
        });
        form.reset();
        setMemoMode(form, "auto");
        await refresh("已新增固定便笺");
        return;
      }
      await api("/api/memos", {
        method: "POST",
        body: JSON.stringify(data),
      });
      form.reset();
      await refresh("备忘已自动分类，进入今日整理");
      return;
    }
    if (kind === "summary") {
      if (!String(data.content || "").trim()) {
        showToast("请输入摘要内容");
        return;
      }
      await api("/api/import/summary", {
        method: "POST",
        body: JSON.stringify(data),
      });
      form.reset();
      await refresh("摘要已导入");
      return;
    }
    if (kind === "settings") {
      await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(data),
      });
      await refresh("设置已保存");
      return;
    }
    if (kind === "pinned-slot") {
      await api(`/api/pinned-slots/${encodeURIComponent(form.dataset.id)}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      await refresh("固定便笺已保存");
    }
  } catch (error) {
    showToast(error.message);
  }
});

window.addEventListener("hashchange", () => {
  const view = window.location.hash.replace("#", "");
  if (titleByView[view]) {
    state.view = view;
    render();
  }
});

async function boot() {
  const initial = window.location.hash.replace("#", "");
  if (titleByView[initial]) {
    state.view = initial;
  }
  try {
    await refresh();
  } catch (error) {
    root.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
  window.setInterval(checkTaskNotifications, 60 * 1000);
}

boot();
