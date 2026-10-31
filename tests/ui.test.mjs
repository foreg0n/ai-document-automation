import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import {pathToFileURL} from "node:url";

// The optional override supports an offline copy of the same development DOM.
const {parseHTML} = await import(process.env.AI_DOC_UI_DOM ? pathToFileURL(process.env.AI_DOC_UI_DOM).href : "linkedom");
const root = new URL("../", import.meta.url);
const html = await readFile(new URL("document_automation/web_static/index.html", root), "utf8");
const script = await readFile(new URL("document_automation/web_static/app.js", root), "utf8");
const manifest = JSON.parse(await readFile(new URL("data/ground_truth.json", root), "utf8"));
const flush = async () => { for (let i = 0; i < 16; i++) await new Promise(resolve => setImmediate(resolve)); };

function harness(source = html, code = script) {
  const {window, document} = parseHTML(source), downloads = [], timers = [], scrolled = [];
  // LinkeDOM does not emulate browser layout, dialogs, or these native behaviors.
  window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.id || this.className); };
  window.HTMLElement.prototype.focus = function () {};
  Object.defineProperty(window.HTMLInputElement.prototype, "checked", {configurable:true, get() {return this.hasAttribute("checked");}, set(value) {this.toggleAttribute("checked", !!value);}});
  Object.defineProperty(window.HTMLSelectElement.prototype, "value", {configurable:true,
    get() {return this.querySelector("option[selected]")?.value ?? this.querySelector("option")?.value ?? "";},
    set(value) {for (const option of this.querySelectorAll("option")) option.toggleAttribute("selected", option.value === String(value));}});
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.open = false;
    dialog.showModal = () => {dialog.open = true; dialog.setAttribute("open", "");};
    dialog.close = () => {dialog.open = false; dialog.removeAttribute("open"); dialog.dispatchEvent(new window.Event("close"));};
  }
  window.HTMLAnchorElement.prototype.click = function () { downloads.push({filename:this.download, href:this.href}); };
  const records = new Map(manifest.cases.slice(0, 4).map((item, index) => [index + 1, {
    id:index + 1, source_file:item.file, state:"completed", fields:{...item.expected_fields}, original_fields:{...item.expected_fields},
    review_revision:0, review_status:"unreviewed", note:"", input_kind:"reference_fixture", model:null,
    pages:[{page_number:1, text:"INVOICE\nSupplier: " + item.expected_fields.supplier + "\nInvoice: " + item.expected_fields.invoice_number + "\nDate: " + item.expected_fields.invoice_date + "\nTotal: " + (item.expected_fields.total_amount || "Not shown")}],
    attempts:[], reviews:[], validation:{requires_review:index===3, status:index===3?"needs_review":"passed", issues:index===3?[{field:"total_amount",code:"missing_required",message:"Total was not found. Check the source."}]:[]},
  }]));
  const server = {listFailure:false, downloadFailure:false, uploads:0, polls:0};
  const clone = value => JSON.parse(JSON.stringify(value));
  const response = (value, status = 200) => ({ok:status >= 200 && status < 300, status,
    json:async () => clone(value), blob:async () => new Blob([typeof value === "string" ? value : JSON.stringify(value)])});
  const fetch = async (input, options = {}) => {
    const url = new URL(input, "http://127.0.0.1");
    if (url.pathname === "/api/documents") {
      if (server.listFailure) throw new TypeError("Failed to fetch");
      const all = [...records.values()], filter = url.searchParams.get("status"), query = (url.searchParams.get("q") || "").toLowerCase();
      const items = all.filter(row => (filter !== "pending" || row.review_status !== "confirmed") && (filter !== "confirmed" || row.review_status === "confirmed") && (filter !== "needs_review" || row.validation.requires_review) && (!query || JSON.stringify(row.fields).toLowerCase().includes(query) || row.source_file.toLowerCase().includes(query)));
      return response({items,total:items.length,page:Number(url.searchParams.get("page")) || 1,page_size:8,demo:true,counts:{total:all.length,pending:all.filter(row=>row.review_status!=="confirmed").length,confirmed:all.filter(row=>row.review_status==="confirmed").length,needs_review:1}});
    }
    if (url.pathname === "/api/export/preview") {
      const rows = [...records.values()].filter(row => (url.searchParams.get("scope") !== "confirmed" || row.review_status === "confirmed") && (!url.searchParams.has("document_id") || row.id === Number(url.searchParams.get("document_id"))));
      return response({count:rows.length,unreviewed:rows.filter(row=>row.review_status!=="confirmed").length,samples:rows.slice(0,5)});
    }
    if (/\/api\/export\.(csv|json)$/.test(url.pathname)) return server.downloadFailure ? response({error:"The download could not be prepared."},503) : response("document_id,supplier\n1,Northstar\n");
    if (url.pathname.startsWith("/api/jobs/")) {server.polls++;return response(server.polls < 3 ? {state:"processing",stage:server.polls===1?"reading":"extracting"} : {state:"completed",stage:"ready",document_id:1,duplicate:true});}
    const match = url.pathname.match(/^\/api\/documents\/(\d+)(\/review|\/pdf)?$/);
    if (match) {
      const record = records.get(Number(match[1])); if (!record) return response({error:"Document not found."},404);
      if (match[2] === "/pdf") return response("%PDF-1.4");
      if (match[2] === "/review") {
        const payload = JSON.parse(options.body);
        if (payload.expected_revision !== record.review_revision) return response({error:"Review changed in another tab."},409);
        record.fields = payload.fields; record.note = payload.note; record.review_revision++;
        record.review_status = payload.confirmed ? "confirmed" : "draft";
        record.reviews.push({confirmed:payload.confirmed,note:payload.note,fields:payload.fields,created_at:"2026-10-31T12:00:00Z"});
      }
      return response(record);
    }
    throw new Error("Unexpected request: " + input);
  };
  const listeners = new Map(); let hash = "";
  const location = {get hash() {return hash;}, set hash(value) {hash=value;queueMicrotask(()=>{for(const listener of listeners.get("hashchange")||[])listener();});}};
  class Upload {
    constructor() {this.upload={addEventListener:(name, callback)=>this.onProgress=callback};}
    open() {} setRequestHeader() {}
    send() {server.uploads++;this.status=202;this.responseText=JSON.stringify({id:"job-test"});queueMicrotask(()=>{this.onProgress?.({lengthComputable:true,loaded:100,total:100});this.onload();});}
  }
  const globals = {document,console,location,fetch,XMLHttpRequest:Upload,URL,URLSearchParams,AbortController,Blob,TypeError,
    setTimeout:(callback, milliseconds) => {const timer=setTimeout(callback,milliseconds===900?0:milliseconds);timer.unref();timers.push(timer);return timer;},
    clearTimeout,scrollTo:()=>{},addEventListener:(name,callback)=>{if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(callback);}};
  globals.window = globals;
  const context = vm.createContext(globals); vm.runInContext(code,context);
  return {document,window,context,server,records,downloads,scrolled,
    navigate:async path=>{location.hash="#"+path;await flush();},
    change:(id,value)=>{const input=document.getElementById(id);input.value=value;input.dispatchEvent(new window.Event("change",{bubbles:true}));},
    input:(id,value)=>{const input=document.getElementById(id);input.value=value;input.dispatchEvent(new window.Event("input",{bubbles:true}));},
    click:id=>document.getElementById(id).click(),
    dispose:()=>timers.forEach(clearTimeout)};
}

test("dashboard renders real rows, a single main action, and status labels",async t=>{
  const app=harness();t.after(app.dispose);await flush();
  assert.equal(app.document.querySelectorAll("#document-list tr").length,4);
  assert.match(app.document.getElementById("document-list").textContent,/Needs review/);
  const visible=[...app.document.querySelectorAll(".button.primary")].filter(item=>!item.closest("[hidden]")&&!item.closest("dialog"));
  assert.equal(visible.length,1);assert.equal(visible[0].id,"upload-document");
});
test("review shows unscored confidence and locates source text without markup injection",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");
  assert.equal(app.document.getElementById("document-detail").hidden,false);
  assert.match(app.document.getElementById("field-inputs").textContent,/Not scored/);
  app.document.getElementById("field-supplier").dispatchEvent(new app.window.Event("focus"));
  assert.equal(app.document.querySelector("#source-text mark").textContent,"Northstar Office Demo");
  app.input("field-supplier",'<img src=x onerror="alert(1)">');
  assert.equal(app.document.querySelector("#field-inputs img"),null);
});
test("confirmation stays disabled until the acknowledgement and required note",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/4");
  assert.equal(app.document.getElementById("confirm-review").disabled,true);
  const check=app.document.getElementById("acknowledge");check.checked=true;check.dispatchEvent(new app.window.Event("change"));
  assert.equal(app.document.getElementById("confirm-review").disabled,true);
  app.input("review-note","The source has no total; this needs follow-up.");
  assert.equal(app.document.getElementById("confirm-review").disabled,false);
});
test("draft save and Undo restore a prior revision using server concurrency checks",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");
  app.input("field-supplier","Corrected supplier");app.click("save-draft");await flush();
  assert.equal(app.records.get(1).fields.supplier,"Corrected supplier");assert.equal(app.records.get(1).review_revision,1);
  app.click("undo-toast");await flush();
  assert.equal(app.records.get(1).fields.supplier,"Northstar Office Demo");assert.equal(app.records.get(1).review_revision,2);
});
test("confirm and export downloads only the reviewed document",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");
  const check=app.document.getElementById("acknowledge");check.checked=true;check.dispatchEvent(new app.window.Event("change"));
  app.document.getElementById("review-form").dispatchEvent(new app.window.Event("submit",{bubbles:true,cancelable:true}));await flush();
  assert.equal(app.records.get(1).review_status,"confirmed");assert.equal(app.downloads[0].filename,"invoice-results-1.csv");
  assert.match(app.document.getElementById("toast-message").textContent,/CSV downloaded/);
});
test("bulk export needs a count-specific confirmation and cancel does not download",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("exports");
  app.change("export-scope","all");await flush();app.click("export-button");await flush();
  assert.equal(app.document.getElementById("confirm-dialog").open,true);
  assert.equal(app.document.getElementById("modal-confirm").textContent,"Export 4 documents");
  app.click("modal-cancel");await flush();assert.equal(app.downloads.length,0);
  app.click("export-button");await flush();app.click("modal-confirm");await flush();assert.equal(app.downloads.length,1);
});
test("upload reports backend stages and reaches review after completion",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("upload");
  const input=app.document.getElementById("file-input");input.files=[{name:"invoice.pdf",size:1024}];input.dispatchEvent(new app.window.Event("change"));
  app.click("process-button");for(let i=0;i<4;i++){await new Promise(resolve=>setTimeout(resolve,5));await flush();}
  assert.equal(app.server.uploads,1);assert.equal(app.server.polls,3);
  assert.equal(app.document.getElementById("view-review").hidden,false);
});
test("a failed document list renders recovery rather than an empty successful screen",async t=>{
  const app=harness();t.after(app.dispose);await flush();app.server.listFailure=true;
  app.click("refresh");await flush();assert.equal(app.document.getElementById("list-error").hidden,false);
  assert.equal(app.document.getElementById("documents-table-wrap").hidden,true);
  app.server.listFailure=false;app.click("retry-documents");await flush();assert.equal(app.document.getElementById("list-error").hidden,true);
});
test("a failed download preserves the successful confirmation and explains recovery",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");app.server.downloadFailure=true;
  const check=app.document.getElementById("acknowledge");check.checked=true;check.dispatchEvent(new app.window.Event("change"));
  app.document.getElementById("review-form").dispatchEvent(new app.window.Event("submit",{bubbles:true,cancelable:true}));await flush();
  assert.equal(app.records.get(1).review_status,"confirmed");assert.match(app.document.getElementById("review-save-error").textContent,/Review saved/);
});
test("component workbench renders every component with eight named states",async t=>{
  const source=await readFile(new URL("document_automation/web_static/design-system.html",root),"utf8");
  const code=await readFile(new URL("document_automation/web_static/design-system.js",root),"utf8");
  const app=harness(source,code);t.after(app.dispose);let count=0;
  for(const group of ["Actions","Forms","Navigation","Data","Intake"]){
    app.change("component-group",group);
    for(const option of [...app.document.getElementById("component-name").options]){
      app.change("component-name",option.value);assert.equal(app.document.querySelectorAll("#component-states .spec-state").length,8);count++;
    }
  }
  assert.equal(count,16);
  for(const screen of ["empty","loading","error","success"]){app.change("screen-state",screen);assert.equal(app.document.querySelectorAll("#screen-patterns article").length,4);}
});
test("invalid edited dates get a local correction message and recover when fixed",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");
  app.input("field-invoice_date","2026-02-30");
  const input=app.document.getElementById("field-invoice_date");input.dispatchEvent(new app.window.Event("blur"));
  assert.equal(input.getAttribute("aria-invalid"),"true");
  assert.match(app.document.getElementById("field-invoice_date-hint").textContent,/YYYY-MM-DD/);
  app.input("field-invoice_date","2026-02-28");assert.equal(input.hasAttribute("aria-invalid"),false);
});
test("JSON selection changes the preview and downloads a JSON file",async t=>{
  const app=harness();t.after(app.dispose);await flush();app.records.get(1).review_status="confirmed";await app.navigate("exports");
  for(const input of app.document.querySelectorAll('input[name="export-format"]'))input.checked=input.value==="json";
  app.document.querySelector('input[value="json"]').dispatchEvent(new app.window.Event("change"));
  assert.ok(app.document.querySelector("#export-preview-content pre"));app.click("export-button");await flush();
  assert.equal(app.downloads[0].filename,"invoice-results.json");
});
test("the review format preference supports JSON without an extra export screen",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");app.change("review-export-format","json");
  const check=app.document.getElementById("acknowledge");check.checked=true;check.dispatchEvent(new app.window.Event("change"));
  app.document.getElementById("review-form").dispatchEvent(new app.window.Event("submit",{bubbles:true,cancelable:true}));await flush();
  assert.equal(app.downloads[0].filename,"invoice-results-1.json");
});
test("field focus preserves the page position and explicit source lookup has a return action",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");
  app.document.getElementById("field-supplier").dispatchEvent(new app.window.Event("focus"));
  assert.deepEqual(app.scrolled,[]);
  app.context.matchMedia=()=>({matches:true});app.document.querySelector(".source-link").click();
  assert.equal(app.document.getElementById("return-to-field").hidden,false);
  assert.equal(app.scrolled.at(-1),"card source-section");
  app.click("return-to-field");assert.equal(app.scrolled.at(-1),"field-supplier");
  assert.equal(app.document.getElementById("return-to-field").hidden,true);
});
test("switching documents clears source-match feedback from the previous invoice",async t=>{
  const app=harness();t.after(app.dispose);await flush();await app.navigate("review/1");
  app.document.getElementById("field-supplier").dispatchEvent(new app.window.Event("focus"));
  assert.match(app.document.getElementById("source-match-status").textContent,/First text match/);
  await app.navigate("review/2");assert.match(app.document.getElementById("source-match-status").textContent,/Select a field/);
  assert.equal(app.document.querySelector("#source-text mark"),null);
});
