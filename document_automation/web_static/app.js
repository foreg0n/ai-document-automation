"use strict";
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const labels = {supplier:"Supplier", invoice_number:"Invoice number", invoice_date:"Invoice date", total_amount:"Total amount", currency:"Currency"};
const state = {view:"documents", filter:"all", query:"", page:1, selected:null, target:null, document:null, file:null,
  demo:false, data:null, dirty:false, saving:false, uploading:false, job:null, pdfUrl:null, listSequence:0, detailSequence:0, exportSequence:0, exportPreview:null, sourceField:null};
let toastTimer, toastAction, searchTimer, toastError = false;

function el(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = text;
  if (className) item.className = className;
  return item;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "/static/icons.svg#" + name); svg.append(use); return svg;
}
function announce(message) { $("announcer").textContent = message; }
function friendly(error) {
  if (error.name === "AbortError") return "The workspace took too long to respond. Check the connection and try again.";
  if (error instanceof TypeError) return "Cannot reach the workspace. Check that the server is running, then try again.";
  if (/LLM_MODEL|LLM_API_KEY|API key is required/i.test(error.message || "")) return "Model access is not configured. Add a model and a valid key to the workspace configuration, then retry.";
  if (/OCR|no extractable text/i.test(error.message || "")) return "This PDF has no readable text layer. Use a text-based PDF or add OCR before uploading it.";
  if (/SQLite|Traceback|HTTP 5\d\d/i.test(error.message || "")) return "The workspace could not complete the action. Check the local service, then try again.";
  return error.message || "The action could not be completed. Please try again.";
}
async function api(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, {...options, signal:controller.signal,
      headers:{"X-CSRF-Token":csrf, ...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof data.error === "string" ? data.error : "The workspace could not complete the action. Try again.");
      error.status = response.status; throw error;
    }
    return data;
  } finally { clearTimeout(timeout); }
}
async function apiBlob(url) {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(url, {signal:controller.signal});
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || "The file could not be prepared. Try again."); }
    return await response.blob();
  } finally { clearTimeout(timeout); }
}
function toast(message, error = false, action = null) {
  clearTimeout(toastTimer); toastAction = action; toastError = error;
  $("toast-message").textContent = message; $("toast").classList.toggle("error", error);
  $("toast-icon").replaceChildren(icon(error ? "warning" : "check-circle").firstChild);
  $("undo-toast").hidden = !action; $("undo-toast").textContent = action?.label || "Undo";
  $("toast").hidden = false; scheduleToast();
}
function scheduleToast() {
  clearTimeout(toastTimer);
  if (!toastError) toastTimer = setTimeout(() => { $("toast").hidden = true; toastAction = null; }, 10000);
}
function showError(error) { toast(friendly(error), true); }
async function ask(title, description, verb, destructive = false) {
  const dialog = $("confirm-dialog");
  if (dialog.open) return false;
  $("modal-title").textContent = title; $("modal-description").textContent = description;
  $("modal-confirm").textContent = verb; $("modal-confirm").className = "button " + (destructive ? "destructive" : "primary");
  dialog.querySelector(".modal-symbol").replaceChildren(icon(destructive ? "trash" : "download"));
  return new Promise(resolve => {
    let accepted = false;
    const finish = () => { $("modal-confirm").removeEventListener("click", confirm); $("modal-cancel").removeEventListener("click", cancel); resolve(accepted); };
    const confirm = () => { accepted = true; dialog.close(); };
    const cancel = () => dialog.close();
    $("modal-confirm").addEventListener("click", confirm); $("modal-cancel").addEventListener("click", cancel);
    dialog.addEventListener("close", finish, {once:true}); dialog.showModal(); $("modal-cancel").focus();
  });
}
function go(hash) {
  const target = hash.startsWith("#") ? hash : "#" + hash;
  if (location.hash === target) route().catch(showError); else location.hash = target;
}
function showView(view) {
  state.view = view;
  for (const name of ["documents", "upload", "review", "exports"]) $("view-" + name).hidden = name !== view;
  const active = view === "exports" ? "exports" : view === "documents" && state.filter === "pending" ? "queue" : "documents";
  for (const button of document.querySelectorAll("[data-nav]")) {
    const selected = button.dataset.nav === active;
    button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  }
  $("breadcrumb-current").textContent = {documents:active === "queue" ? "Review queue" : "Documents", upload:"Upload", review:"Review", exports:"Export"}[view];
  document.title = $("breadcrumb-current").textContent + " · AI Document Automation";
  window.scrollTo(0, 0);
}
async function route() {
  const raw = (location.hash || "#documents").slice(1), [path, query = ""] = raw.split("?");
  if (path.startsWith("review/")) {
    const id = Number(path.split("/")[1]);
    if (!Number.isInteger(id) || id < 1) { go("documents"); return; }
    if (state.dirty && state.selected !== id) {
      const leave = await ask("Discard unsaved changes?", "Your saved review stays intact. Changes in the current form will be lost.", "Discard changes", true);
      if (!leave) { go("review/" + state.selected); return; }
      state.dirty = false;
    }
    state.target = id; showView("review");
    if (state.selected === id && state.document && state.dirty) return;
    await selectDocument(id); return;
  }
  if (path === "upload") { showView("upload"); updateFile(); return; }
  if (path === "exports") { showView("exports"); updateSelectedExport(); await loadExportPreview(); return; }
  const requested = new URLSearchParams(query).get("status");
  state.filter = path === "queue" ? "pending" : ["all","pending","confirmed","needs_review"].includes(requested) ? requested : "all";
  $("status-filter").value = state.filter; state.page = 1;
  $("documents-title").textContent = state.filter === "pending" ? "Review queue" : "Documents";
  $("list-heading").textContent = {all:"All documents",pending:"Awaiting review",needs_review:"Needs attention",confirmed:"Confirmed documents"}[state.filter];
  showView("documents"); await refresh();
}
function statusFor(record) {
  if (record.state === "failed") return {label:"Processing failed", cls:"failed", icon:"warning"};
  if (record.state !== "completed") return {label:"Processing", cls:"", icon:"clock"};
  if (record.review_status === "confirmed") return {label:"Confirmed", cls:"confirmed", icon:"check-circle"};
  if (record.validation?.requires_review) return {label:"Needs review", cls:"needs-review", icon:"warning"};
  if (record.review_status === "draft") return {label:"Draft saved", cls:"", icon:"document"};
  return {label:"Awaiting review", cls:"", icon:"clock"};
}
function makeBadge(record) {
  const status = statusFor(record), badge = el("span", undefined, "badge " + status.cls);
  badge.append(icon(status.icon), el("span", status.label)); return badge;
}
function skeletonRows(container, count = 6, columns = 5) {
  container.replaceChildren();
  for (let row = 0; row < count; row++) {
    const tr = el("tr", undefined, "skeleton-row");
    for (let column = 0; column < columns; column++) { const td = el("td"); td.append(el("div", undefined, "skeleton skeleton-line")); tr.append(td); }
    container.append(tr);
  }
}
function updateEnvironment(data) {
  state.demo = data.demo;
  $("demo-banner").hidden = !data.demo;
  $("environment").textContent = data.demo ? "Reference demo · no model calls" : "Personal workspace";
  $("sidebar-status").textContent = data.demo ? "Demo data" : "Connected";
  $("upload-help").textContent = data.demo ? "Included sample invoice PDFs, up to 20 MB." : "Text-based invoice PDFs, up to 20 MB.";
  $("upload-availability").textContent = data.demo ? "Use a PDF from data/samples. Your review edits are saved." : "Image-only scans need OCR before they can be processed.";
  $("processing-notice").textContent = data.demo ? "The saved reference result will be reused. No model call." : "The PDF text is sent to your configured model.";
  for (const [id, value] of [["nav-total",data.counts.total],["nav-pending",data.counts.pending],["pending-count",data.counts.pending],["flagged-count",data.counts.needs_review],["confirmed-count",data.counts.confirmed],["list-count",data.total]]) $(id).textContent = value;
}
async function refresh() {
  const sequence = ++state.listSequence;
  $("list-error").hidden = true; $("list-empty").hidden = true; $("documents-table-wrap").hidden = false;
  $("document-list").setAttribute("aria-busy", "true"); skeletonRows($("document-list"));
  $("refresh").disabled = true; $("refresh").title = "Refreshing documents";
  try {
    const params = new URLSearchParams({status:state.filter, q:state.query, page:state.page});
    const data = await api("/api/documents?" + params);
    if (sequence !== state.listSequence) return;
    state.data = data; updateEnvironment(data); renderRows(data);
    $("page-label").textContent = data.total ? "Page " + data.page + " of " + Math.ceil(data.total / data.page_size) + " · " + data.total + " documents" : "0 documents";
    $("previous-page").disabled = data.page <= 1;
    $("next-page").disabled = data.page * data.page_size >= data.total;
    $("previous-page").title = data.page <= 1 ? "You are on the first page" : "Previous page";
    $("next-page").title = $("next-page").disabled ? "There is no next page" : "Next page";
  } catch (error) {
    if (sequence !== state.listSequence) return;
    $("documents-table-wrap").hidden = true; $("list-error").hidden = false;
    $("list-error-message").textContent = friendly(error);
    $("page-label").textContent = "Documents unavailable"; $("sidebar-status").textContent = "Connection unavailable";
    throw error;
  } finally {
    if (sequence === state.listSequence) { $("document-list").removeAttribute("aria-busy"); $("refresh").disabled = false; $("refresh").title = "Refresh documents"; }
  }
}
function renderRows(data) {
  const body = $("document-list"); body.replaceChildren();
  $("documents-table-wrap").hidden = !data.items.length; $("list-empty").hidden = !!data.items.length;
  if (!data.items.length) {
    const filtered = !!state.query || state.filter !== "all";
    $("list-empty-title").textContent = filtered ? "No documents in this view" : "Your documents will appear here";
    $("list-empty-description").textContent = filtered ? "Try a different search or clear the filters." : "Choose Upload document to add your first invoice.";
    $("clear-filters").hidden = !filtered;
  }
  for (const record of data.items) {
    const row = el("tr"); row.dataset.id = record.id; const fields = record.fields || {};
    const title = el("button", undefined, "document-open"); title.type = "button";
    const glyph = el("span", undefined, "file-symbol"); glyph.append(icon("document"));
    const copy = el("span"); copy.append(el("strong", fields.supplier || record.source_file), el("small", fields.invoice_number || record.source_file, "mono"));
    title.append(glyph, copy); title.addEventListener("click", () => go("review/" + record.id));
    title.title = "Review " + record.source_file; const first = el("td"); first.append(title);
    const status = el("td"); status.append(makeBadge(record));
    const amount = el("td", undefined, "numeric");
    if (fields.total_amount == null) amount.append(el("span", "Not found", "missing-value"));
    else amount.textContent = fields.total_amount + " " + (fields.currency || "");
    const date = el("td", fields.invoice_date || "Not found", "date-column");
    const last = el("td"); const open = el("button", undefined, "button ghost icon-button");
    open.append(icon("arrow-right")); open.title = "Review " + record.source_file; open.setAttribute("aria-label", open.title);
    open.addEventListener("click", () => go("review/" + record.id)); last.append(open);
    row.append(first, status, amount, date, last); body.append(row);
  }
}
async function selectDocument(id) {
  const sequence = ++state.detailSequence;
  $("review-loading").hidden = false; $("review-error").hidden = true; $("document-detail").hidden = true;
  document.querySelector(".more-menu").hidden = true;
  try {
    const record = await api("/api/documents/" + id);
    if (sequence !== state.detailSequence) return;
    renderDocument(record); announce("Review " + record.source_file);
  } catch (error) {
    if (sequence !== state.detailSequence) return;
    $("review-error").hidden = false; $("review-error-message").textContent = friendly(error); throw error;
  } finally { if (sequence === state.detailSequence) $("review-loading").hidden = true; }
}
function renderDocument(record) {
  state.selected = record.id; state.document = record; state.dirty = false;
  state.sourceField = null; $("return-to-field").hidden = true;
  document.querySelector(".more-menu").hidden = false; document.querySelector(".more-menu").open = false;
  if (state.pdfUrl) { URL.revokeObjectURL(state.pdfUrl); state.pdfUrl = null; }
  $("source-pdf").removeAttribute("src"); showSourceTab("text");
  $("pdf-tab").disabled = false; $("pdf-tab").removeAttribute("aria-busy");
  $("document-detail").hidden = false; $("detail-filename").textContent = record.source_file;
  $("detail-subtitle").textContent = "Document " + String(record.id).padStart(3, "0") + " · " + record.pages.length + (record.pages.length === 1 ? " page" : " pages") + " · " + (record.review_status === "confirmed" ? "Review confirmed" : "Check against the source");
  const badge = makeBadge(record); $("detail-status").className = badge.className; $("detail-status").replaceChildren(...badge.childNodes);
  $("open-pdf").href = "/api/documents/" + record.id + "/pdf";
  renderSource();
  $("source-match-status").textContent = "Select a field to find its value in the text.";
  const summary = $("rule-summary"), issues = record.validation?.issues || [];
  summary.replaceChildren(); summary.className = "rule-summary " + (issues.length || record.state !== "completed" ? "flagged" : "passed");
  summary.append(icon(issues.length || record.state !== "completed" ? "warning" : "check-circle"));
  const summaryCopy = el("div");
  summaryCopy.append(el("strong", record.state !== "completed" ? "Processing did not finish" : issues.length ? "Source review needed" : "Format checks passed"));
  summaryCopy.append(el("p", record.state !== "completed" ? "Fix the processing issue, then upload this PDF again." : issues.length ? "Check the highlighted fields. Leave missing values blank." : "A valid format does not verify the facts in the document."));
  summary.append(summaryCopy);
  const container = $("field-inputs"); container.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const wrapper = el("div", undefined, "form-field");
    const caption = el("div", undefined, "field-caption");
    const labelNode = el("label", label); labelNode.htmlFor = "field-" + key;
    const fieldIssues = issues.filter(issue => issue.field === key);
    const confidence = el("span", undefined, "confidence" + (fieldIssues.length ? " warning" : ""));
    confidence.title = "The extractor does not provide calibrated confidence scores.";
    confidence.append(icon(fieldIssues.length ? "warning" : "info"), el("span", fieldIssues.length ? "Check source" : "Not scored"));
    caption.append(labelNode, confidence);
    const input = el("input", undefined, "field-input"); input.id = "field-" + key; input.name = key;
    input.value = record.fields?.[key] ?? ""; input.autocomplete = "off"; input.maxLength = 4000;
    input.disabled = record.state !== "completed";
    if (key !== "supplier") input.dataset.mono = "true";
    if (key === "total_amount") input.inputMode = "decimal";
    if (key === "invoice_date") input.placeholder = "YYYY-MM-DD";
    if (fieldIssues.length) input.dataset.state = "warning";
    const hint = el("span", undefined, "field-message" + (fieldIssues.length ? " warning" : ""));
    hint.id = "field-" + key + "-hint";
    const suggestions = {supplier:"Use the supplier name printed on the invoice.", invoice_number:"Keep the invoice identifier exactly as printed.", invoice_date:"Use YYYY-MM-DD.", total_amount:"Two decimal places, without grouping. Leave blank if missing.", currency:"Three uppercase letters: PLN, EUR, or USD."};
    if (fieldIssues.length) hint.append(icon("warning"), el("span", fieldIssues.map(item => item.message).join(" ")));
    else hint.textContent = suggestions[key];
    input.setAttribute("aria-describedby", hint.id);
    const original = el("span", "Original: " + (record.original_fields?.[key] ?? "Not found"), "original-value");
    const updateOriginal = () => original.hidden = input.value === (record.original_fields?.[key] ?? "");
    const find = el("button", "Find in source", "source-link"); find.type = "button"; find.addEventListener("click", () => findInSource(key, true));
    const checkInput = () => {
      const problem = fieldProblem(key, input.value);
      hint.replaceChildren(); input.removeAttribute("data-state"); input.removeAttribute("aria-invalid");
      hint.className = "field-message";
      if (problem) { input.dataset.state = problem.kind; hint.classList.add(problem.kind); hint.append(icon("warning"), el("span", problem.message)); if (problem.kind === "error") input.setAttribute("aria-invalid", "true"); }
      else hint.textContent = suggestions[key];
    };
    input.addEventListener("input", () => { state.dirty = true; updateOriginal(); if (input.dataset.touched) checkInput(); updateConfirm(); });
    input.addEventListener("blur", () => { input.dataset.touched = "true"; checkInput(); findInSource(key); });
    input.addEventListener("focus", () => findInSource(key)); updateOriginal();
    wrapper.append(caption, input, hint, original, find); container.append(wrapper);
  }
  $("review-note").value = record.note || ""; $("acknowledge").checked = false;
  $("note-error").hidden = true; $("review-save-error").hidden = true;
  $("original-fields").textContent = JSON.stringify(record.original_fields, null, 2);
  $("save-draft").disabled = record.state !== "completed"; $("save-draft").title = record.state !== "completed" ? "Processing must finish before a draft can be saved" : "Save a review without confirming";
  $("review-export-format").value = exportFormat(); renderAudit(record); updateConfirm(); updateSelectedExport();
}
function renderSource(needle = null) {
  const source = $("source-text"); source.replaceChildren(); let matched = false, pageFound = null;
  for (const page of state.document?.pages || []) {
    source.append(el("h3", "PAGE " + page.page_number));
    const pre = el("pre"), text = page.text || "No text was extracted from this page.";
    const index = needle && !matched ? text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase()) : -1;
    if (index >= 0) {
      pre.append(document.createTextNode(text.slice(0, index)), el("mark", text.slice(index, index + needle.length)), document.createTextNode(text.slice(index + needle.length)));
      matched = true; pageFound = page.page_number;
    } else pre.textContent = text;
    source.append(pre);
  }
  if (!state.document?.pages.length) source.append(el("p", "No extracted text is available. Open the original PDF to check the source.", "metadata"));
  return pageFound;
}
function sourceCandidates(key, value) {
  const candidates = [value];
  if (key === "total_amount" && /^\d+\.\d{2}$/.test(value)) {
    const [whole, fraction] = value.split(".");
    candidates.push(value.replace(".", ","), whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + "," + fraction,
      whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + fraction);
  }
  if (key === "invoice_date" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [year, month, day] = value.split("-"); candidates.push(day + "." + month + "." + year, day + "/" + month + "/" + year);
  }
  return candidates.filter(Boolean);
}
function findInSource(key, reveal = false) {
  if (!state.document) return;
  const value = $("field-" + key).value.trim();
  const pages = state.document.pages || [];
  const found = sourceCandidates(key, value).find(candidate => pages.some(page => page.text.toLocaleLowerCase().includes(candidate.toLocaleLowerCase())));
  if (reveal) showSourceTab("text");
  const page = renderSource(found || null);
  $("source-match-status").textContent = page ? "First text match · Page " + page + ". Check its context." : value ? "No exact text match. Check the original PDF." : "No value to locate. Check the source before adding one.";
  if (page && $("source-pdf").hidden) {
    const source = $("source-text"), mark = source.querySelector("mark");
    // Input focus scrolls only the source panel, preserving the form viewport.
    if (mark && typeof mark.getBoundingClientRect === "function") {
      source.scrollTop += mark.getBoundingClientRect().top - source.getBoundingClientRect().top - 24;
    }
  }
  if (reveal) {
    state.sourceField = key;
    $("return-to-field").hidden = !window.matchMedia?.("(max-width: 1000px)").matches;
    $("return-to-field").title = "Return to the " + labels[key].toLowerCase() + " field";
    document.querySelector(".source-section").scrollIntoView({block:"nearest"});
  }
}
function showSourceTab(name) {
  const text = name === "text";
  $("source-text").hidden = !text; $("source-pdf").hidden = text;
  for (const id of ["text", "pdf"]) {
    const active = id === name; $(id + "-tab").classList.toggle("active", active);
    $(id + "-tab").setAttribute("aria-selected", String(active)); $(id + "-tab").tabIndex = active ? 0 : -1;
  }
}
async function openPDF() {
  if (!state.selected) return;
  const documentId = state.selected, sequence = state.detailSequence;
  $("pdf-tab").setAttribute("aria-busy", "true"); $("pdf-tab").disabled = true;
  try {
    if (!state.pdfUrl) {
      const blob = await apiBlob("/api/documents/" + documentId + "/pdf");
      if (state.selected !== documentId || state.detailSequence !== sequence) return;
      state.pdfUrl = URL.createObjectURL(blob);
    }
    $("source-pdf").src = state.pdfUrl + "#toolbar=0"; showSourceTab("pdf");
  } catch (error) { if (state.selected === documentId && state.detailSequence === sequence) { $("source-match-status").textContent = friendly(error); showError(error); } }
  finally { if (state.selected === documentId && state.detailSequence === sequence) { $("pdf-tab").removeAttribute("aria-busy"); $("pdf-tab").disabled = false; } }
}
function renderAudit(record) {
  const container = $("audit-history"); container.replaceChildren();
  $("audit-title").textContent = "Review history · " + record.reviews.length + (record.reviews.length === 1 ? " revision" : " revisions");
  if (!record.reviews.length) container.append(el("p", "No saved reviews yet.", "audit-entry metadata"));
  for (const review of [...record.reviews].reverse()) {
    const entry = el("div", undefined, "audit-entry");
    entry.append(el("strong", review.confirmed ? "Review confirmed" : "Draft saved"),
      el("p", new Date(review.created_at).toLocaleString(), "mono metadata"), el("p", review.note || "No review note."));
    container.append(entry);
  }
  if (record.attempts.length) {
    const group = el("details", undefined, "audit-entry"); group.append(el("summary", "Processing attempts · " + record.attempts.length));
    for (const attempt of [...record.attempts].reverse()) group.append(el("p", "Attempt " + attempt.id + " · " + attempt.state + " · " + new Date(attempt.started_at).toLocaleString()));
    container.append(group);
  }
}
function currentFields() {
  return Object.fromEntries(Object.keys(labels).map(key => [key, $("field-" + key).value === "" ? null : $("field-" + key).value]));
}
function fieldProblem(key, value) {
  if (!value || !value.trim()) return {kind:"warning", message:"No value found. Check the source; leave blank if it is missing."};
  if (key === "invoice_date") {
    const timestamp = /^\d{4}-\d{2}-\d{2}$/.test(value) ? Date.parse(value + "T00:00:00Z") : NaN;
    if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) return {kind:"error", message:"Use a real date in YYYY-MM-DD format, for example 2026-10-20."};
  }
  if (key === "total_amount" && (!/^\d+\.\d{2}$/.test(value) || Number(value) <= 0)) return {kind:"error", message:"Use a positive amount with two decimal places, for example 1234.50. Leave blank if missing."};
  if (key === "currency" && !["PLN","EUR","USD"].includes(value)) return {kind:"warning", message:"Use PLN, EUR, or USD. Explain another currency in the review note."};
  return null;
}
function unresolved(fields) {
  const original = state.document?.fields || {};
  return Object.keys(labels).some(key => fieldProblem(key, fields[key])) || (state.document?.validation?.issues || []).some(issue => !issue.field || fields[issue.field] === original[issue.field] || !fields[issue.field]);
}
function updateConfirm() {
  if (!state.document) return;
  const remaining = unresolved(currentFields()), noteMissing = remaining && !$("review-note").value.trim();
  const acknowledged = $("acknowledge").checked;
  const reason = state.document.state !== "completed" ? "Processing must finish before a review can be confirmed."
    : !acknowledged ? "Check the source acknowledgement to confirm and export."
    : noteMissing ? "Add a review note explaining the remaining checks." : "Confirmation saves your review and downloads this document as " + exportFormat().toUpperCase() + ".";
  $("confirm-review").disabled = state.saving || state.document.state !== "completed" || !acknowledged || noteMissing;
  $("confirm-review").title = reason; $("confirm-help").textContent = reason;
  $("review-note").setAttribute("aria-invalid", String(acknowledged && noteMissing));
  $("note-error").hidden = !acknowledged || !noteMissing;
  $("note-error").textContent = "Explain why you are confirming a document with unresolved checks.";
}
async function download(format, params) {
  const blob = await apiBlob("/api/export." + format + "?" + new URLSearchParams(params)), url = URL.createObjectURL(blob);
  const link = el("a"); link.href = url; link.download = "invoice-results" + (params.document_id ? "-" + params.document_id : "") + "." + format;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
}
async function saveReview(confirmed) {
  if (!state.document || state.saving || state.document.state !== "completed") return;
  if (confirmed && $("confirm-review").disabled) { updateConfirm(); return; }
  const id = state.selected, before = {fields:{...state.document.fields}, confirmed:state.document.review_status === "confirmed", note:state.document.note || ""};
  const format = exportFormat();
  const payload = {fields:currentFields(), expected_revision:state.document.review_revision, confirmed, acknowledged:$("acknowledge").checked, note:$("review-note").value};
  state.saving = true; $("save-draft").disabled = true; $("confirm-review").disabled = true;
  const active = confirmed ? $("confirm-review") : $("save-draft"); active.setAttribute("aria-busy", "true");
  const oldChildren = [...active.childNodes].map(child => child.cloneNode(true)); active.textContent = confirmed ? "Confirming…" : "Saving…";
  $("review-save-error").hidden = true; let saved = false, downloaded = false;
  try {
    const record = await api("/api/documents/" + id + "/review", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    saved = true;
    if (state.selected === id) renderDocument(record);
    const undo = async () => {
      if (state.selected === id && state.dirty && !await ask("Replace unsaved changes?", "Restoring the previous saved review will replace the changes in this form.", "Restore review")) return;
      const restored = await api("/api/documents/" + id + "/review", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({...before, expected_revision:record.review_revision, acknowledged:before.confirmed})});
      if (state.selected === id) renderDocument(restored);
      await refresh(); toast(confirmed ? "Previous values and confirmation restored. The downloaded copy is unchanged." : "Previous values restored.");
    };
    if (confirmed) { await download(format, {scope:"confirmed", document_id:id}); downloaded = true; }
    await refresh(); toast(confirmed ? "Review confirmed. " + format.toUpperCase() + " downloaded." : "Draft saved.", false, {label:"Undo", run:undo});
  } catch (error) {
    const message = saved ? confirmed && !downloaded ? "Review saved. The download did not finish. Open Export to try again." : "Review saved. The document list could not refresh. Try Refresh to reconnect." : error.status === 409 ? "This review changed in another tab. Refresh the document before saving again." : friendly(error);
    $("review-save-error").textContent = message; $("review-save-error").hidden = false; toast(message, true);
  } finally {
    state.saving = false; active.removeAttribute("aria-busy"); active.replaceChildren(...oldChildren);
    $("save-draft").disabled = state.document?.state !== "completed"; updateConfirm();
  }
}
function updateFile() {
  const selected = !!state.file;
  $("selected-file").hidden = !selected; $("upload-actions").hidden = !selected || state.uploading;
  $("choose-file").className = "button " + (selected ? "secondary" : "primary");
  $("choose-file").textContent = selected ? "Choose another PDF" : "Choose PDF";
  $("choose-file").disabled = state.uploading; $("remove-file").disabled = state.uploading;
  $("choose-file").title = state.uploading ? "Wait for the current document to finish" : "Choose a PDF up to 20 MB";
  $("remove-file").title = state.uploading ? "The current document is processing" : "Remove selected PDF";
  $("process-button").disabled = !selected || state.uploading;
  if (selected) { $("selected-filename").textContent = state.file.name; $("selected-size").textContent = (state.file.size / 1024 / 1024).toFixed(2) + " MB · PDF"; }
}
function chooseFile(file) {
  if (!file || state.uploading) return;
  if (!file.name.toLowerCase().endsWith(".pdf") || !file.size || file.size > 20 * 1024 * 1024) {
    state.file = null; state.job = null; updateFile();
    $("upload-error-title").textContent = "Choose a supported document";
    $("upload-error-message").textContent = "Use a non-empty PDF up to 20 MB. Image-only scans need OCR before processing.";
    $("upload-error").hidden = false; $("retry-upload").textContent = "Choose PDF"; $("retry-upload").dataset.choose = "true"; return;
  }
  state.file = file; state.job = null; $("upload-error").hidden = true; $("job-status").hidden = true; updateFile(); announce("Selected " + file.name);
}
function progress(stage, percent = null) {
  $("job-status").hidden = false;
  const steps = ["upload","reading","extracting","ready"], active = stage === "reusing" || stage === "validating" ? 2 : Math.max(0, steps.indexOf(stage));
  const copy = {upload:["Uploading document","Transferred bytes are shown below."],queued:["Waiting in the queue","Your PDF is queued. You can keep using the workspace."],reading:["Reading the PDF","Checking the text layer. Image-only scans require OCR."],extracting:["Extracting invoice details","Waiting for the configured model. No percentage is invented for this step."],validating:["Checking the extracted details","Checking field formats before review."],reusing:["Reusing saved details","An exact duplicate was found. No new model call is needed."],ready:["Ready to review","The extracted details are ready for your source check."]};
  const [title, description] = copy[stage] || copy.queued;
  $("progress-title").textContent = title; $("progress-description").textContent = description;
  $("upload-percent").textContent = percent === null ? "" : percent + "%";
  if (stage === "ready") $("upload-progress").value = 100;
  else if (stage === "upload" && percent !== null) $("upload-progress").value = percent;
  else $("upload-progress").removeAttribute("value");
  for (const item of document.querySelectorAll("[data-step]")) {
    const index = steps.indexOf(item.dataset.step); item.classList.toggle("complete", index < active || stage === "ready"); item.classList.toggle("current", index === active && stage !== "ready");
  }
}
function sendFile(file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest(); request.open("POST", "/api/uploads?" + new URLSearchParams({filename:file.name}));
    request.timeout = 60000; request.setRequestHeader("X-CSRF-Token", csrf); request.setRequestHeader("Content-Type", "application/pdf");
    request.upload.addEventListener("progress", event => { if (event.lengthComputable) progress("upload", Math.round(event.loaded / event.total * 100)); });
    request.onload = () => {
      let data; try { data = JSON.parse(request.responseText); } catch { reject(new Error("The upload response could not be read. Check the workspace and try again.")); return; }
      if (request.status < 200 || request.status >= 300) reject(new Error(data.error || "The upload was not accepted. Check the PDF and try again.")); else resolve(data);
    };
    request.onerror = () => reject(new Error("The upload connection was interrupted. Check the workspace, then try again."));
    request.ontimeout = () => reject(new Error("The upload took too long. Check the file size and try again.")); request.send(file);
  });
}
async function pollJob() {
  const deadline = Date.now() + 360000;
  while (Date.now() < deadline) {
    const job = await api("/api/jobs/" + state.job.id);
    if (job.state === "failed") { state.job = null; throw new Error(job.error || "Processing stopped. Check the PDF and try again."); }
    if (job.state === "completed") return job;
    progress(job.state === "queued" ? "queued" : job.stage || "reading");
    await new Promise(resolve => setTimeout(resolve, 900));
  }
  throw new Error("Processing is still taking time. It may continue in the background. Choose Check status to reconnect.");
}
async function upload() {
  if (!state.file || state.uploading) return;
  state.uploading = true; $("upload-error").hidden = true; updateFile();
  try {
    if (!state.job) { progress("upload", 0); state.job = await sendFile(state.file); }
    const job = await pollJob(); progress("ready", 100);
    state.file = null; state.job = null; $("file-input").value = "";
    await refresh();
    if (state.view === "upload") go("review/" + job.document_id);
    toast(job.duplicate ? "Saved details reused. The document is ready to review." : "Document ready to review.", false,
      {label:"Review", run:async () => go("review/" + job.document_id)});
  } catch (error) {
    $("upload-error-title").textContent = state.job ? "Processing status is unavailable" : "This document could not be processed";
    $("upload-error-message").textContent = friendly(error); $("upload-error").hidden = false;
    $("retry-upload").textContent = state.job ? "Check status" : "Try again"; $("retry-upload").dataset.choose = "false";
    $("job-status").hidden = true;
  } finally { state.uploading = false; updateFile(); }
}
function updateSelectedExport() {
  const selected = $("selected-export-option"); selected.disabled = !state.document || state.document.state !== "completed";
  selected.textContent = selected.disabled ? "Current document — select one first" : "Current document · " + state.document.source_file;
  if (selected.disabled && $("export-scope").value === "selected") $("export-scope").value = "confirmed";
}
function exportParams() { return $("export-scope").value === "selected" ? {scope:"all", document_id:state.selected} : {scope:$("export-scope").value}; }
function exportFormat() { return document.querySelector('input[name="export-format"]:checked').value; }
async function loadExportPreview() {
  const sequence = ++state.exportSequence; state.exportPreview = null;
  $("export-button").disabled = true; $("export-help").textContent = "Loading available documents…";
  $("export-error").hidden = true; $("export-empty").hidden = true; $("export-warning").hidden = true;
  const container = $("export-preview-content"); container.replaceChildren(el("div", undefined, "skeleton-panel"));
  for (let i = 0; i < 5; i++) container.firstChild.append(el("div", undefined, "skeleton skeleton-field"));
  try {
    const preview = await api("/api/export/preview?" + new URLSearchParams(exportParams()));
    if (sequence !== state.exportSequence) return;
    state.exportPreview = preview; renderExportPreview(preview); updateExportButton();
  } catch (error) {
    if (sequence !== state.exportSequence) return;
    container.replaceChildren(); $("export-error").hidden = false; $("export-error-message").textContent = friendly(error);
    $("export-help").textContent = "Load an export preview before downloading."; throw error;
  }
}
function updateExportButton() {
  const count = state.exportPreview?.count || 0;
  $("export-button-label").textContent = "Download " + exportFormat().toUpperCase();
  $("export-button").disabled = !count; $("export-button").title = count ? "Download " + count + (count === 1 ? " document" : " documents") : "Choose at least one completed document";
  $("export-help").textContent = count ? count + (count === 1 ? " document. " : " documents. ") + "A local file will be downloaded." : "No documents in this scope. Confirm a review or choose all completed documents.";
}
function renderExportPreview(preview) {
  $("export-count").textContent = preview.count + (preview.count === 1 ? " document" : " documents");
  $("export-empty").hidden = !!preview.count;
  $("export-warning").hidden = !preview.unreviewed;
  $("export-warning").replaceChildren(icon("warning"), el("span", preview.unreviewed + (preview.unreviewed === 1 ? " document has not been confirmed." : " documents have not been confirmed.") + " Check the source before using these values."));
  const container = $("export-preview-content"); container.replaceChildren();
  if (!preview.count) return;
  if (exportFormat() === "json") {
    container.append(el("pre", JSON.stringify({documents:preview.samples.map(row => ({document_id:row.document_id, fields:row.fields}))}, null, 2), "code-block"));
  } else {
    const scroll = el("div", undefined, "table-scroll"), table = el("table", undefined, "document-table"), head = el("thead"), tr = el("tr");
    for (const name of ["Supplier","Invoice","Amount"]) { const th = el("th", name); th.scope = "col"; tr.append(th); }
    head.append(tr); table.append(head); const body = el("tbody");
    for (const row of preview.samples) {
      const record = el("tr"); record.append(el("td", row.fields.supplier || "Not found"),
        el("td", row.fields.invoice_number || "Not found", "mono"),
        el("td", row.fields.total_amount == null ? "Not found" : row.fields.total_amount + " " + (row.fields.currency || ""), "numeric")); body.append(record);
    }
    table.append(body); scroll.append(table); container.append(scroll);
  }
  if (preview.count > preview.samples.length) { const note = el("p", "Preview shows the first " + preview.samples.length + " documents. The download includes all " + preview.count + ".", "quiet-note"); note.style.padding = "0 24px 24px"; container.append(note); }
}
async function exportDocuments() {
  if (!state.exportPreview?.count) return;
  const preview = state.exportPreview, format = exportFormat();
  if (preview.count > 1 && !await ask("Export " + preview.count + " documents?",
    "Download " + preview.count + " documents as " + format.toUpperCase() + ". " + (preview.unreviewed ? preview.unreviewed + " are not confirmed. " : "") + "The file will be saved by your browser.", "Export " + preview.count + " documents")) return;
  $("export-button").disabled = true; $("export-button").setAttribute("aria-busy", "true"); $("export-button-label").textContent = "Preparing download…";
  try { await download(format, exportParams()); toast(format.toUpperCase() + " downloaded. " + preview.count + (preview.count === 1 ? " document." : " documents.")); }
  catch (error) { $("export-error").hidden = false; $("export-error-message").textContent = friendly(error); showError(error); }
  finally { $("export-button").removeAttribute("aria-busy"); updateExportButton(); }
}
for (const button of document.querySelectorAll("[data-nav]")) button.addEventListener("click", () => go(button.dataset.nav));
for (const button of document.querySelectorAll("[data-back]")) button.addEventListener("click", () => go("documents"));
for (const button of document.querySelectorAll("[data-overview]")) button.addEventListener("click", () => go("documents?status=" + button.dataset.overview));
$("upload-document").addEventListener("click", () => go("upload"));
$("choose-file").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", event => chooseFile(event.target.files[0]));
$("remove-file").addEventListener("click", () => {
  const previous = state.file; state.file = null; state.job = null; $("file-input").value = ""; $("upload-error").hidden = true; $("job-status").hidden = true; updateFile();
  toast("PDF removed from this upload.", false, {label:"Undo", run:async () => { state.file = previous; updateFile(); }});
});
$("process-button").addEventListener("click", upload);
$("retry-upload").addEventListener("click", () => $("retry-upload").dataset.choose === "true" ? $("file-input").click() : upload());
for (const type of ["dragenter","dragover"]) $("dropzone").addEventListener(type, event => { event.preventDefault(); if (!state.uploading) $("dropzone").classList.add("dragging"); });
for (const type of ["dragleave","drop"]) $("dropzone").addEventListener(type, event => {
  event.preventDefault(); $("dropzone").classList.remove("dragging");
  if (type === "drop") { if (event.dataTransfer.files.length > 1) toast("Choose one PDF at a time.", true); else chooseFile(event.dataTransfer.files[0]); }
});
$("review-form").addEventListener("submit", event => { event.preventDefault(); saveReview(true); });
$("save-draft").addEventListener("click", () => saveReview(false));
$("acknowledge").addEventListener("change", updateConfirm);
$("review-note").addEventListener("input", () => { state.dirty = true; updateConfirm(); });
$("show-history").addEventListener("click", event => { event.target.closest("details").open = false; $("audit").open = true; $("audit").scrollIntoView({block:"nearest"}); });
$("show-original").addEventListener("click", event => { event.target.closest("details").open = false; $("original-panel").open = true; $("original-panel").scrollIntoView({block:"nearest"}); });
$("pdf-tab").addEventListener("click", openPDF); $("text-tab").addEventListener("click", () => showSourceTab("text"));
$("return-to-field").addEventListener("click", () => {
  if (!state.sourceField) return;
  const input = $("field-" + state.sourceField);
  input.focus({preventScroll:true}); input.scrollIntoView({block:"center"}); $("return-to-field").hidden = true;
});
for (const id of ["text-tab","pdf-tab"]) $(id).addEventListener("keydown", event => {
  if (["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? $("text-tab") : event.key === "End" ? $("pdf-tab") : $(id === "text-tab" ? "pdf-tab" : "text-tab"); next.focus(); next.click(); }
});
$("refresh").addEventListener("click", () => refresh().then(() => toast("Documents refreshed.")).catch(showError));
$("retry-documents").addEventListener("click", () => refresh().catch(showError));
$("retry-review").addEventListener("click", () => selectDocument(state.target).catch(showError));
$("status-filter").addEventListener("change", event => go("documents?status=" + event.target.value));
$("clear-filters").addEventListener("click", () => { state.query = ""; $("search").value = ""; go("documents"); });
$("search").addEventListener("input", event => {
  clearTimeout(searchTimer); state.query = event.target.value; state.page = 1;
  searchTimer = setTimeout(() => { if (state.view !== "documents") go("documents"); else refresh().catch(showError); }, 250);
});
$("previous-page").addEventListener("click", () => { state.page--; refresh().catch(showError); });
$("next-page").addEventListener("click", () => { state.page++; refresh().catch(showError); });
$("export-scope").addEventListener("change", () => loadExportPreview().catch(showError));
for (const input of document.querySelectorAll('input[name="export-format"]')) input.addEventListener("change", () => { $("review-export-format").value = exportFormat(); if (state.exportPreview) renderExportPreview(state.exportPreview); updateExportButton(); if (state.document) updateConfirm(); });
$("review-export-format").addEventListener("change", event => { for (const input of document.querySelectorAll('input[name="export-format"]')) input.checked = input.value === event.target.value; if (state.exportPreview) renderExportPreview(state.exportPreview); updateExportButton(); updateConfirm(); });
$("retry-export").addEventListener("click", () => loadExportPreview().catch(showError));
$("export-button").addEventListener("click", exportDocuments);
$("return-to-queue").addEventListener("click", () => go("queue"));
$("dismiss-toast").addEventListener("click", () => { $("toast").hidden = true; clearTimeout(toastTimer); toastAction = null; });
$("undo-toast").addEventListener("click", async () => {
  const action = toastAction; if (!action) return;
  $("undo-toast").disabled = true; clearTimeout(toastTimer);
  try { await action.run(); } catch (error) { showError(error); } finally { $("undo-toast").disabled = false; }
});
for (const event of ["mouseenter","focusin"]) $("toast").addEventListener(event, () => clearTimeout(toastTimer));
for (const event of ["mouseleave","focusout"]) $("toast").addEventListener(event, scheduleToast);
$("help-button").addEventListener("click", () => $("help-dialog").showModal());
$("close-help").addEventListener("click", () => $("help-dialog").close());
document.addEventListener("click", event => { for (const menu of document.querySelectorAll(".more-menu[open]")) if (!menu.contains(event.target)) menu.open = false; });
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !document.querySelector("dialog[open]")) { event.preventDefault(); $("search").focus(); }
  if (event.key === "Escape") for (const menu of document.querySelectorAll(".more-menu[open]")) menu.open = false;
});
window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("hashchange", () => route().catch(showError));
async function boot() {
  if ((location.hash || "#documents").startsWith("#documents") || location.hash === "#queue") await route();
  else { await refresh(); await route(); }
}
boot().catch(showError);
