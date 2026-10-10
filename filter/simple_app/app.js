const $ = id => document.getElementById(id);
let token = '', columns = [], busy = false;
let loadedFileName = '';
const labels = {eq:'Equals (text)',ne:'Not equals (text)',contains:'Contains text',not_contains:'Does not contain text',gt:'Greater than (number)',ge:'Greater than or equal (number)',lt:'Less than (number)',le:'Less than or equal (number)',empty:'Is missing',not_empty:'Is not missing'};
const el = (tag, text) => {const n = document.createElement(tag); if(text !== undefined) n.textContent = text; return n;};
function message(text, error=false) { $('message').textContent=text; $('message').className=error?'error':'ok'; }
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'X-Local-Token':token,...options.headers}});
  if(!response.ok) {const data=await response.json();throw new Error(data.error || 'Request failed.');}
  return response;
}
async function run(task) {
  if(busy)return; busy=true;
  document.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{await task();}catch(e){message(e.message,true);}
  finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}
}
function select(options) {const s=el('select');for(const [value,text] of options){const o=el('option',text);o.value=value;s.append(o);}return s;}
function checkbox(checked=false){const c=el('input');c.type='checkbox';c.checked=checked;return c;}
function buildColumns(){
  $('columns').replaceChildren();$('rules').replaceChildren();
  for(const name of columns){
    const row=el('tr');row.append(el('td',name));
    for(let i=0;i<4;i++){const td=el('td');const c=checkbox(i===0);c.setAttribute('aria-label',name+' '+['Keep','Drop if missing','Deduplication key','Fill missing'][i]);td.append(c);row.append(td);}
    const td=el('td'),input=el('input');input.type='text';input.placeholder='Constant value (as text)';input.setAttribute('aria-label',name+' Fill value');td.append(input);row.append(td);$('columns').append(row);
  }
}
function addRule(){
  const row=el('div');row.className='rule';const col=select(columns.map(c=>[c,c])),op=select(Object.entries(labels)),value=el('input'),remove=el('button','Remove');
  col.setAttribute('aria-label','Filter column');op.setAttribute('aria-label','Comparison');value.placeholder='Filter value';value.setAttribute('aria-label','Filter value');remove.className='secondary';remove.onclick=()=>row.remove();
  op.onchange=()=>{value.disabled=['empty','not_empty'].includes(op.value);};row.append(col,op,value,remove);$('rules').append(row);
}
function render(data,result){
  $('previewSection').hidden=false;$('previewTitle').textContent=result?'Cleaned results':'Original file preview';$('download').hidden=!result;
  $('previewNote').textContent=`${data.total.toLocaleString('en-US')} rows · ${data.columns.length} columns. Preview shows the first 100 rows; the download includes all cleaned rows.`;
  const table=$('preview');table.replaceChildren();const head=el('thead'),hr=el('tr');data.columns.forEach(c=>hr.append(el('th',c)));head.append(hr);table.append(head);
  const body=el('tbody');data.rows.forEach(row=>{const tr=el('tr');row.forEach(v=>tr.append(el('td',v)));body.append(tr);});table.append(body);
  $('stats').replaceChildren();if(data.stats){const names={input_rows:'Input rows',filtered_rows:'Filtered out',missing_removed:'Missing rows removed',duplicates_removed:'Duplicates removed',filled_cells:'Cells filled',output_rows:'Output rows'};for(const [key,title]of Object.entries(names)){const card=el('div');card.append(el('strong',data.stats[key].toLocaleString('en-US')),el('span',title));$('stats').append(card);}}
}
$('upload').onclick=()=>run(async()=>{
  const file=$('file').files[0];if(!file)throw new Error('Please choose a CSV or TSV file.');if(file.size>50*1024*1024)throw new Error('The file exceeds 50 MB.');
  message('Reading the file locally…');const response=await api('/api/upload',{method:'POST',headers:{'X-Encoding':$('encoding').value,'X-Delimiter':$('delimiter').value},body:file});const data=await response.json();columns=data.columns;buildColumns();$('settings').hidden=false;loadedFileName=file.name;updateSourceInfo(data.total);render(data,false);message('File loaded. Configure your cleaning rules.');
});
$('addRule').onclick=addRule;
$('process').onclick=()=>run(async()=>{
  const config={columns:[],drop_missing:[],dedupe:[],fills:{},trim:$('trim').checked,missing_tokens:$('missing').value.split(/\r?\n/),match:$('match').value,rules:[]};
  [...$('columns').children].forEach((row,i)=>{const inputs=row.querySelectorAll('input');if(inputs[0].checked)config.columns.push(columns[i]);if(inputs[1].checked)config.drop_missing.push(columns[i]);if(inputs[2].checked)config.dedupe.push(columns[i]);if(inputs[3].checked)config.fills[columns[i]]=inputs[4].value;});
  for(const row of $('rules').children){const fields=row.querySelectorAll('select,input');config.rules.push({column:fields[0].value,op:fields[1].value,value:fields[2].value});}
  message('Processing locally…');$('download').hidden=true;const response=await api('/api/process',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});render(await response.json(),true);message('Processing complete. Preview and download your results below.');
});
$('download').onclick=()=>run(async()=>{const response=await api('/api/download');const blob=await response.blob();const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download='cleaned.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);message('Download requested. Save the file in a local folder without cloud sync.');});
$('clear').onclick=()=>run(async()=>{await api('/api/clear',{method:'POST',body:''});columns=[];loadedFileName='';$('file').value='';$('pickedFile').textContent='No file loaded.';$('settings').hidden=true;$('previewSection').hidden=true;$('preview').replaceChildren();$('columns').replaceChildren();$('rules').replaceChildren();updateSourceInfo();message('Session data cleared.');});
$('chooseFile').onclick=()=>$('file').click();
$('file').onchange=()=>{const picked=$('file').files[0];$('pickedFile').textContent=picked?picked.name:'No file loaded.';if(picked?.name.toLowerCase().endsWith('.tsv'))$('delimiter').value='tab';};
fetch('/api/session').then(r=>r.json()).then(d=>{token=d.token;}).catch(()=>message('Cannot connect to the local backend.',true));

function updateSourceInfo(total) {
  $('sourceInfo').textContent = loadedFileName
    ? `Loaded ${loadedFileName} · ${total.toLocaleString('en-US')} rows · ${columns.length} columns`
    : 'No file loaded. All fields are read as text to preserve leading zeros in identifiers.';
}
