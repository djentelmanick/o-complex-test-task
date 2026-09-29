"use strict";

const state = { apiKey: "", leads: [] };
const $ = (id) => document.getElementById(id);

const ERROR_MESSAGES = {
  401: "Неверный API-ключ",
  404: "Лид не найден",
  413: "Слишком длинное сообщение",
  422: "Проверьте текст сообщения",
  429: "Слишком много запросов — подождите минуту",
  503: "Языковая модель временно недоступна, попробуйте позже",
};

const ROLE_LABELS = { client: "Клиент", manager: "Менеджер" };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-API-Key": state.apiKey },
  });
  if (!response.ok) {
    throw new Error(ERROR_MESSAGES[response.status] || `Ошибка ${response.status}`);
  }
  return response.json();
}

function showError(message) {
  const el = $("error");
  el.textContent = message || "";
  el.hidden = !message;
}

function renderDialog(lead) {
  const list = $("dialog");
  list.replaceChildren();
  for (const message of lead.dialog) {
    const item = document.createElement("li");
    item.className = `bubble ${message.role}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = ROLE_LABELS[message.role] || message.role;
    const text = document.createElement("p");
    text.textContent = message.text;
    item.append(who, text);
    list.append(item);
  }
}

function renderLeads() {
  const select = $("lead-select");
  select.replaceChildren();
  for (const lead of state.leads) {
    const option = document.createElement("option");
    option.value = lead.id;
    option.textContent = lead.name;
    select.append(option);
  }
  select.disabled = false;
  renderDialog(state.leads[0]);
}

function setText(id, value) {
  const el = $(id);
  el.textContent = value;
  el.classList.remove("muted");
}

function renderAnswer(data) {
  setText("client-reply", data.client_reply);
  setText("manager-hint", data.manager_hint);

  const sources = $("sources");
  sources.replaceChildren();
  for (const source of data.sources) {
    const item = document.createElement("li");
    item.textContent = source.title;
    sources.append(item);
  }
  $("sources-block").hidden = data.sources.length === 0;
  $("copy").disabled = false;

  const u = data.usage;
  const fallback = data.fallback ? " · автоответ не сформирован" : "";
  $("meta").textContent =
    `Токены: ${u.prompt_tokens} + ${u.completion_tokens} = ${u.total_tokens} · ${data.latency_ms} мс${fallback}`;
}

function setLoading(isLoading) {
  const button = $("send");
  button.disabled = isLoading;
  button.textContent = isLoading ? "Генерирую…" : "Сгенерировать ответ";
  button.classList.toggle("loading", isLoading);
}

$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.apiKey = $("api-key").value.trim();
  showError("");
  try {
    state.leads = await api("/api/v1/leads");
    renderLeads();
    $("message").disabled = false;
    $("send").disabled = false;
  } catch (error) {
    showError(error.message);
  }
});

$("lead-select").addEventListener("change", (event) => {
  const lead = state.leads.find((item) => item.id === event.target.value);
  if (lead) renderDialog(lead);
});

$("message").addEventListener("input", (event) => {
  $("counter").textContent = `${event.target.value.length} / 2000`;
});

$("inquiry-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  setLoading(true);
  try {
    const data = await api("/api/v1/inquiries", {
      method: "POST",
      body: JSON.stringify({ lead_id: $("lead-select").value, message: $("message").value }),
    });
    renderAnswer(data);
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
});

$("copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("client-reply").textContent);
  $("copy").textContent = "Скопировано";
  setTimeout(() => { $("copy").textContent = "Скопировать"; }, 1500);
});
