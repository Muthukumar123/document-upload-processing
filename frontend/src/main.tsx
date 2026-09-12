import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import ExcelJS from "exceljs";
import "./styles.css";

const API = import.meta.env.VITE_API_BASE_URL ?? "/api";
type Prepared = {batchId:string;documents:{documentId:string;fileName:string;uploadUrl:string}[]};

async function sha256(file: Blob) {
  const b = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2,"0")).join("");
}
async function templateWorkbook(rows=153) {
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet("Cases");
  ws.addRow(Array.from({length:10},(_,i)=>`Column${i+1}`));
  for(let r=1;r<=rows;r++) ws.addRow([`KEY-${r}`,`VALUE-${r}`,`Name ${r}`,"NL","Amsterdam","1000AA","TypeA","Active",r,new Date().toISOString()]);
  return new Blob([await wb.xlsx.writeBuffer()],{type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"});
}

function App(){
  const [files,setFiles]=useState<File[]>([]);
  const [status,setStatus]=useState("Ready");
  const [progress,setProgress]=useState(0);
  const [batchId,setBatchId]=useState<string>();
  const [result,setResult]=useState<any>();
  const pct=useMemo(()=>Math.round(progress*100),[progress]);

  async function run(input: File[]){
    if(!input.length)return;
    setStatus("Preparing upload"); setProgress(0); setResult(undefined);
    const prep=await fetch(`${API}/uploads/prepare`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({files:input.map(f=>({name:f.name,size:f.size}))})});
    if(!prep.ok) throw new Error(await prep.text());
    const p=await prep.json() as Prepared; setBatchId(p.batchId);
    for(let i=0;i<input.length;i++){
      const f=input[i],d=p.documents[i];
      setStatus(`Uploading ${i+1}/${input.length}: ${f.name}`);
      const upload=await fetch(d.uploadUrl,{method:"PUT",headers:{"x-ms-blob-type":"BlockBlob","content-type":f.type||"application/octet-stream"},body:f});
      if(!upload.ok) throw new Error(`Blob upload failed: ${upload.status}`);
      const complete=await fetch(`${API}/uploads/complete`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({batchId:p.batchId,documentId:d.documentId,sha256:await sha256(f)})});
      if(!complete.ok) throw new Error(await complete.text());
      setProgress((i+1)/input.length);
    }
    setStatus("Queued. Polling processing status");
    for(let n=0;n<240;n++){
      const r=await fetch(`${API}/batches/${p.batchId}`);
      if(r.ok){
        const j=await r.json(); setResult(j);
        if(["COMPLETED","PARTIAL_SUCCESS"].includes(j.Status)){setStatus(`Finished: ${j.Status}`);return;}
      }
      await new Promise(x=>setTimeout(x,5000));
    }
    setStatus("Upload completed; processing is still running");
  }

  async function generate(){
    setStatus("Generating one 153-row Excel template");
    const blob=await templateWorkbook(153);
    const generated=Array.from({length:153},(_,i)=>new File([blob],`batch-document-${String(i+1).padStart(3,"0")}.xlsx`,{type:blob.type}));
    setFiles(generated); setStatus("153 test documents generated");
  }

  return <main>
    <header><h1>Document Upload Processing</h1><p>Batch upload, Service Bus processing, SQL comparison, validation and audit tracking.</p></header>
    <section className="card">
      <h2>Upload documents</h2>
      <input type="file" multiple accept=".xlsx" onChange={e=>setFiles(Array.from(e.target.files??[]))}/>
      <div className="actions"><button onClick={generate}>Generate 153-document test</button><button className="primary" disabled={!files.length} onClick={()=>run(files).catch(e=>setStatus(`Error: ${e.message}`))}>Upload & process {files.length||""}</button></div>
      <div className="meter"><span style={{width:`${pct}%`}}/></div>
      <p><strong>Status:</strong> {status}</p>{batchId&&<p><strong>Batch:</strong> {batchId}</p>}
    </section>
    {result&&<section className="card"><h2>Processing result</h2><pre>{JSON.stringify(result,null,2)}</pre></section>}
    <section className="card"><h2>Test profile</h2><p>The generated test creates 153 Excel documents, each containing 153 data rows and 10 columns. Documents are uploaded directly to Azure Blob Storage using short-lived SAS URLs, then queued for bounded asynchronous processing.</p></section>
  </main>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
