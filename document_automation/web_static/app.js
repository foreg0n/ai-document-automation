"use strict";
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const $ = id => document.getElementById(id);
const state = {filter:"all", query:"", page:1, selected:null, document:null, file:null, demo:false, loading:false};
const labels = {supplier:"Supplier", invoice_number:"Invoice number", invoice_date:"Invoice date", total_amount:"Total amount", currency:"Currency"};
let toastTimer;
function toast(message, error=false) {
  $("toast-message").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  if (!error) toastTimer = setTimeout(() => $("toast").hidden = true, 7000);
}
async function api(url, options={}) {
  const response = await fetch(url, {...options, headers:{"X-CSRF-Token":csrf, ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
}
function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}
function badge(record) {
  if (record.state !== "completed") return {label:record.state === "failed" ? "Failed" : "Processing", cls:"failed"};
  if (record.review_status === "confirmed") return {label:"Confirmed", cls:"confirmed"};
  if (record.validation && record.validation.requires_review) return {label:"Needs review", cls:"needs-review"};
  return {label:record.review_status === "draft" ? "Draft saved" : "Pending review", cls:""};
}
async function refresh(selectFirst=false) {
  const params = new URLSearchParams({status:state.filter, q:state.query, page:state.page});
  const data = await api("/api/documents?" + params);
  state.demo = data.demo;
  $("environment").textContent = data.demo ? "DEMO WORKSPACE" : "LOCAL WORKSPACE";
  $("demo-banner").hidden = !data.demo;
  $("upload-help").textContent = data.demo ? "Included sample PDFs only · up to 20 MB · no model calls in this workspace." : "Text-based PDF · up to 20 MB. Processing sends text to your configured model.";
  for (const [id, value] of [["total-count",data.counts.total],["pending-count",data.counts.pending],["confirmed-count",data.counts.confirmed],["flagged-count",data.counts.needs_review],["nav-total",data.counts.total],["nav-pending",data.counts.pending],["nav-confirmed",data.counts.confirmed],["list-count",data.total]]) $(id).textContent = value;
  const list = $("document-list"); list.replaceChildren();
  if (!data.items.length) list.append(node("div", "No documents match this view.", "list-empty"));
  for (const record of data.items) {
    const row = node("button", undefined, "document-row"); row.type = "button"; row.dataset.id = record.id;
    row.classList.toggle("selected", record.id === state.selected);
    const top = node("div", undefined, "row-top");
    const title = node("h3", (record.fields || {}).supplier || record.source_file); title.title = title.textContent;
    const status = badge(record); top.append(title, node("span", status.label, "badge " + status.cls));
    const sub = node("p", record.source_file); sub.title = record.source_file;
    const bottom = node("div", undefined, "row-bottom");
    const fields = record.fields || {};
    bottom.append(node("span", fields.invoice_number || "No invoice number", "caption"), node("span", fields.total_amount === null || fields.total_amount === undefined ? "Total not found" : fields.total_amount + " " + (fields.currency || ""), "amount" + (fields.total_amount == null ? " missing" : "")));
    row.append(top, sub, bottom);
    row.addEventListener("click", () => selectDocument(record.id).catch(showError)); list.append(row);
  }
  $("page-label").textContent = "Page " + data.page + " of " + Math.max(1, Math.ceil(data.total / data.page_size));
  $("previous-page").disabled = data.page <= 1; $("next-page").disabled = data.page * data.page_size >= data.total;
  if (data.items.length && (selectFirst || state.selected === null || !data.items.some(item => item.id === state.selected))) await selectDocument(data.items[0].id);
  else if (!data.items.length) { state.selected = null; state.document = null; $("document-detail").hidden = true; $("empty-detail").hidden = false; }
}
function showError(error) { toast(error.message || "Something went wrong.", true); }
async function selectDocument(id) {
  const record = await api("/api/documents/" + id);
  state.selected = id; state.document = record;
  for (const row of document.querySelectorAll(".document-row")) row.classList.toggle("selected", Number(row.dataset.id) === id);
  $("empty-detail").hidden = true; $("document-detail").hidden = false;
  $("detail-filename").textContent = record.source_file;
  $("detail-subtitle").textContent = "Document #" + id + " · " + (record.input_kind === "reference_fixture" ? "Reference fixture" : record.model || "Local record") + " · " + record.attempts.length + " processing attempt(s)";
  const status = badge(record); $("detail-status").textContent = status.label; $("detail-status").className = "badge " + status.cls;
  const source = $("source-text"); source.replaceChildren();
  for (const page of record.pages) source.append(node("h4", "PAGE " + page.page_number), node("pre", page.text || "No text was extracted from this page."));
  if (!record.pages.length) source.append(node("pre", "No extracted text is available. Check the processing attempt below."));
  $("source-pdf").removeAttribute("src"); $("source-pdf").hidden = true; source.hidden = false;
  $("text-tab").classList.add("active"); $("pdf-tab").classList.remove("active");
  $("open-pdf").href = "/api/documents/" + id + "/pdf";
  const issues = (record.validation || {}).issues || [];
  const summary = $("rule-summary"); summary.replaceChildren(); summary.classList.toggle("flagged", issues.length > 0 || record.state !== "completed");
  summary.append(node("strong", record.state !== "completed" ? "Processing did not complete" : issues.length ? issues.length + " check(s) need attention" : "Format checks passed"));
  if (issues.length) { const list = node("ul"); for (const issue of issues) list.append(node("li", (issue.field ? labels[issue.field] + ": " : "") + issue.message)); summary.append(list); }
  else summary.append(node("span", record.state !== "completed" ? "Upload the PDF again after fixing the processing error." : "Check the source before confirming these values."));
  const inputs = $("field-inputs"); inputs.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const wrapper = node("div"); const caption = node("label", label, "field-label"); caption.htmlFor = "field-" + key;
    const input = node("input", undefined, "field-input"); input.id = "field-" + key; input.name = key; input.value = (record.fields || {})[key] ?? ""; input.autocomplete = "off"; input.maxLength = 4000;
    input.classList.toggle("flagged", issues.some(issue => issue.field === key)); input.disabled = record.state !== "completed";
    if (key === "total_amount") input.inputMode = "decimal";
    if (key === "invoice_date") input.placeholder = "YYYY-MM-DD";
    const original = node("span", "Extracted: " + ((record.original_fields || {})[key] ?? "Not found"), "original-value");
    const updateHint = () => original.hidden = input.value === ((record.original_fields || {})[key] ?? "");
    input.addEventListener("input", updateHint); updateHint(); wrapper.append(caption, input, original); inputs.append(wrapper);
  }
  $("review-note").value = record.note; $("acknowledge").checked = false;
  $("save-draft").disabled = $("confirm-review").disabled = record.state !== "completed";
  const audit = $("audit-history"); audit.replaceChildren();
  $("audit-title").textContent = "Review history (" + record.reviews.length + ") · Processing attempts (" + record.attempts.length + ")";
  for (const review of [...record.reviews].reverse()) {
    const entry = node("div", undefined, "audit-entry"); entry.append(node("strong", (review.confirmed ? "Confirmed" : "Draft") + " · " + new Date(review.created_at).toLocaleString()), node("p", review.note || "No note"), node("pre", JSON.stringify(review.fields, null, 2))); audit.append(entry);
  }
  for (const attempt of [...record.attempts].reverse()) audit.append(node("p", "Attempt #" + attempt.id + " · " + attempt.state + (attempt.error_type ? " · " + attempt.error_type : "") + " · " + new Date(attempt.started_at).toLocaleString()));
}
async function saveReview(confirmed) {
  if (!state.document) return;
  if (confirmed && !$("acknowledge").checked) return toast("Confirm that you checked the values against the document.", true);
  const fields = {}; for (const key of Object.keys(labels)) { const value = $("field-" + key).value; fields[key] = value === "" ? null : value; }
  $("review-panel").classList.add("busy");
  try {
    const id = state.selected;
    await api("/api/documents/" + id + "/review", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({fields, expected_revision:state.document.review_revision, confirmed, acknowledged:$("acknowledge").checked, note:$("review-note").value})});
    await selectDocument(id); await refresh(); toast(confirmed ? "Review confirmed. This document is included in the confirmed export." : "Draft saved. Confirm it when your check is complete.");
  } catch (error) { showError(error); } finally { $("review-panel").classList.remove("busy"); }
}
function chooseFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf") || file.size > 20 * 1024 * 1024) return toast("Choose a PDF up to 20 MB.", true);
  state.file = file; $("upload-title").textContent = file.name; $("process-button").disabled = false;
}
async function upload() {
  if (!state.file) return;
  $("process-button").disabled = true; $("job-status").hidden = false; $("job-status").textContent = "Uploading…";
  try {
    const job = await api("/api/uploads?" + new URLSearchParams({filename:state.file.name}), {method:"POST", headers:{"Content-Type":"application/pdf"}, body:state.file});
    while (true) {
      const current = await api("/api/jobs/" + job.id);
      $("job-status").textContent = current.state === "queued" ? "Queued for processing…" : "Reading the PDF and extracting fields…";
      if (current.state === "failed") throw new Error(current.error);
      if (current.state === "completed") { state.selected = current.document_id; await refresh(); await selectDocument(current.document_id); toast(current.duplicate ? "Exact duplicate found. The saved result was reused." : "Invoice processed. Its fields are ready for your review."); break; }
      await new Promise(resolve => setTimeout(resolve, 900));
    }
    state.file = null; $("file-input").value = ""; $("upload-title").textContent = "Drop an invoice here";
  } catch (error) { showError(error); await refresh().catch(showError); }
  finally { $("job-status").hidden = true; $("process-button").disabled = !state.file; }
}
$("file-input").addEventListener("change", event => chooseFile(event.target.files[0]));
$("choose-file").addEventListener("click", () => $("file-input").click());
$("process-button").addEventListener("click", upload);
for (const type of ["dragenter","dragover"]) $("dropzone").addEventListener(type, event => { event.preventDefault(); $("dropzone").classList.add("dragging"); });
for (const type of ["dragleave","drop"]) $("dropzone").addEventListener(type, event => { event.preventDefault(); $("dropzone").classList.remove("dragging"); if (type === "drop") chooseFile(event.dataTransfer.files[0]); });
$("review-form").addEventListener("submit", event => { event.preventDefault(); saveReview(true); });
$("save-draft").addEventListener("click", () => saveReview(false));
$("refresh").addEventListener("click", () => refresh().then(() => state.selected && selectDocument(state.selected)).catch(showError));
$("dismiss-toast").addEventListener("click", () => $("toast").hidden = true);
$("export-button").addEventListener("click", () => { window.location.href = "/api/export.csv?" + new URLSearchParams({scope:$("export-scope").value}); });
$("mobile-export-button").addEventListener("click", () => { window.location.href = "/api/export.csv?" + new URLSearchParams({scope:$("mobile-export-scope").value}); });
for (const button of document.querySelectorAll("[data-filter]")) button.addEventListener("click", () => { state.filter = button.dataset.filter; state.page = 1; for (const item of document.querySelectorAll("[data-filter]")) item.classList.toggle("active", item === button); refresh(true).catch(showError); });
let searchTimer; $("search").addEventListener("input", event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = event.target.value; state.page = 1; refresh(true).catch(showError); }, 250); });
$("previous-page").addEventListener("click", () => { state.page--; refresh(true).catch(showError); });
$("next-page").addEventListener("click", () => { state.page++; refresh(true).catch(showError); });
$("pdf-tab").addEventListener("click", () => { if (!state.selected) return; $("source-pdf").src = "/api/documents/" + state.selected + "/pdf#toolbar=0"; $("source-pdf").hidden = false; $("source-text").hidden = true; $("pdf-tab").classList.add("active"); $("text-tab").classList.remove("active"); });
$("text-tab").addEventListener("click", () => { $("source-pdf").hidden = true; $("source-text").hidden = false; $("text-tab").classList.add("active"); $("pdf-tab").classList.remove("active"); });
refresh(true).catch(showError);
