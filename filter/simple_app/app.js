const $ = id => document.getElementById(id);
let token = '', columns = [], busy = false;
let currentPreview = null, previewIsResult = false, loadedFileName = '', lastMessage = null;
const labels = {eq:'等于（文本）',ne:'不等于（文本）',contains:'包含文本',not_contains:'不包含文本',gt:'大于（数值）',ge:'大于等于（数值）',lt:'小于（数值）',le:'小于等于（数值）',empty:'为空',not_empty:'非空'};
const el = (tag, text) => {const n = document.createElement(tag); if(text !== undefined) n.textContent = text; return n;};
function message(text, error=false) { lastMessage = {text,error}; $('message').textContent=translatedMessage(text); $('message').className=error?'error':'ok'; }
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'X-Local-Token':token,...options.headers}});
  if(!response.ok) {const data=await response.json();throw new Error(data.error || '请求失败');}
  return response;
}
async function run(task) {
  if(busy)return; busy=true;
  document.querySelectorAll('button:not([id^="lang-"])').forEach(b=>b.disabled=true);
  try{await task();}catch(e){message(e.message,true);}
  finally{busy=false;document.querySelectorAll('button:not([id^="lang-"])').forEach(b=>b.disabled=false);}
}
function select(options) {const s=el('select');for(const [value,text] of options){const o=el('option',text);o.value=value;s.append(o);}return s;}
function checkbox(checked=false){const c=el('input');c.type='checkbox';c.checked=checked;return c;}
function buildColumns(){
  $('columns').replaceChildren();$('rules').replaceChildren();
  for(const name of columns){
    const row=el('tr');row.append(el('td',name));
    for(let i=0;i<4;i++){const td=el('td');const c=checkbox(i===0);translatedAttribute(c,'aria-label',['输出','空值删行','去重键','填补空值'][i],name+' ');td.append(c);row.append(td);}
    const td=el('td'),input=el('input');input.type='text';translatedAttribute(input,'placeholder','固定值（保持文本）');translatedAttribute(input,'aria-label','填充值',name+' ');td.append(input);row.append(td);$('columns').append(row);
  }
}
function addRule(){
  const row=el('div');row.className='rule';const col=select(columns.map(c=>[c,c])),op=select(Object.entries(labels)),value=el('input'),remove=el('button','删除');
  translatedAttribute(col,'aria-label','筛选列');translatedAttribute(op,'aria-label','比较方式');translatedAttribute(value,'placeholder','条件值');translatedAttribute(value,'aria-label','条件值');for(const option of op.options) translatedNode(option,labels[option.value]);translatedNode(remove,'删除');remove.className='secondary';remove.onclick=()=>row.remove();
  op.onchange=()=>{value.disabled=['empty','not_empty'].includes(op.value);};row.append(col,op,value,remove);$('rules').append(row);
}
function render(data,result){
  currentPreview=data; previewIsResult=result;
  $('previewSection').hidden=false;translatedNode($('previewTitle'),result?'处理结果':'原始文件预览');$('download').hidden=!result;
  $('previewNote').textContent=language==='en' ? `${data.total.toLocaleString('en-US')} rows · ${data.columns.length} columns. Preview shows the first 100 rows; the download includes all cleaned rows.` : `共 ${data.total.toLocaleString('zh-CN')} 行 · ${data.columns.length} 列；预览前 100 行，下载包含全部处理结果。`;
  const table=$('preview');table.replaceChildren();const head=el('thead'),hr=el('tr');data.columns.forEach(c=>hr.append(el('th',c)));head.append(hr);table.append(head);
  const body=el('tbody');data.rows.forEach(row=>{const tr=el('tr');row.forEach(v=>tr.append(el('td',v)));body.append(tr);});table.append(body);
  $('stats').replaceChildren();if(data.stats){const names={input_rows:'原始行数',filtered_rows:'筛选移除',missing_removed:'空值移除',duplicates_removed:'重复移除',filled_cells:'填补单元格',output_rows:'结果行数'};for(const [key,title]of Object.entries(names)){const card=el('div');card.append(el('strong',data.stats[key].toLocaleString()),translatedNode(el('span'),title));$('stats').append(card);}}
}
$('upload').onclick=()=>run(async()=>{
  const file=$('file').files[0];if(!file)throw new Error('请选择 CSV 或 TSV 文件。');if(file.size>50*1024*1024)throw new Error('文件超过 50 MB。');
  message('正在本地读取文件…');const response=await api('/api/upload',{method:'POST',headers:{'X-Encoding':$('encoding').value,'X-Delimiter':$('delimiter').value},body:file});const data=await response.json();columns=data.columns;buildColumns();$('settings').hidden=false;loadedFileName=file.name;sourceRowCount=data.total;updateSourceInfo(data.total);render(data,false);message('文件已加载，请设置条件。');
});
$('addRule').onclick=addRule;
$('process').onclick=()=>run(async()=>{
  const config={columns:[],drop_missing:[],dedupe:[],fills:{},trim:$('trim').checked,missing_tokens:$('missing').value.split(/\r?\n/),match:$('match').value,rules:[]};
  [...$('columns').children].forEach((row,i)=>{const inputs=row.querySelectorAll('input');if(inputs[0].checked)config.columns.push(columns[i]);if(inputs[1].checked)config.drop_missing.push(columns[i]);if(inputs[2].checked)config.dedupe.push(columns[i]);if(inputs[3].checked)config.fills[columns[i]]=inputs[4].value;});
  for(const row of $('rules').children){const fields=row.querySelectorAll('select,input');config.rules.push({column:fields[0].value,op:fields[1].value,value:fields[2].value});}
  message('正在本地处理…');$('download').hidden=true;const response=await api('/api/process',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});render(await response.json(),true);message('处理完成。结果可在下方浏览和下载。');
});
$('download').onclick=()=>run(async()=>{const response=await api('/api/download');const blob=await response.blob();const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download='cleaned.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);message('已发起下载，请保存在未启用云同步的本地文件夹。');});
$('clear').onclick=()=>run(async()=>{await api('/api/clear',{method:'POST',body:''});columns=[];currentPreview=null;loadedFileName='';$('file').value='';translatedNode($('pickedFile'),'尚未上传文件。');$('settings').hidden=true;$('previewSection').hidden=true;$('preview').replaceChildren();$('columns').replaceChildren();$('rules').replaceChildren();updateSourceInfo();message('会话数据已清空。');});
$('chooseFile').onclick=()=>$('file').click();
$('file').onchange=()=>{const picked=$('file').files[0];if(picked){$('pickedFile').removeAttribute('data-i18n');$('pickedFile').textContent=picked.name;}else{translatedNode($('pickedFile'),'尚未上传文件。');}if(picked?.name.toLowerCase().endsWith('.tsv'))$('delimiter').value='tab';};
fetch('/api/session').then(r=>r.json()).then(d=>{token=d.token;}).catch(()=>message('无法连接本地后端。',true));

function updateSourceInfo(total) {
  // Filenames and column names are data; interpolate them without translation.
  $('sourceInfo').removeAttribute('data-i18n');
  $('sourceInfo').textContent = loadedFileName
    ? (language === 'en' ? `Loaded ${loadedFileName} · ${total.toLocaleString('en-US')} rows · ${columns.length} columns` : `已加载 ${loadedFileName} · ${total.toLocaleString('zh-CN')} 行 · ${columns.length} 列`)
    : t('尚未上传文件。所有字段先作为文本读取，保留编号前导零。');
}
function setLanguage(next) {
  language = next;
  translateStatic();
  if (currentPreview) {
    const downloadHidden = $('download').hidden;
    render(currentPreview,previewIsResult);
    $('download').hidden = downloadHidden;
  }
  updateSourceInfo(sourceRowCount);
  if (lastMessage) message(lastMessage.text,lastMessage.error);
}
let sourceRowCount = 0;
$('lang-en').onclick=()=>setLanguage('en');
$('lang-zh').onclick=()=>setLanguage('zh');
translateStatic();
