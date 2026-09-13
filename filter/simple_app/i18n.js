const translations = {
  "Local Filter · 数据清洗": "Local Filter · Data Cleaning",
  "数据清洗，留在本机。": "Clean your data. Keep it local.",
  "上传表格、设置规则、预览并下载。所有处理均在此电脑完成。": "Upload a table, set your rules, preview, and download. Everything runs on this computer.",
  "● 本地运行 · 无外部服务": "● Local only · No external services",
  "数据仅保留在程序内存中，关闭程序即释放。代码目录位于 OneDrive；请将下载结果保存到未启用云同步的本地文件夹。此工具不自动判断 AKI 或生成临床标签。": "Data stays in application memory until cleared or the server stops. If your code folder is in OneDrive, save downloaded results outside cloud-synced folders. This tool does not identify AKI or generate clinical labels.",
  "选择文件": "Choose a file",
  "CSV / TSV（≤50 MB，≤20 万行）": "CSV / TSV (up to 50 MB and 200,000 rows)",
  "编码": "Encoding",
  "UTF-8（默认）": "UTF-8 (default)",
  "分隔符": "Delimiter",
  "逗号 ,": "Comma ,",
  "制表符 TSV": "Tab / TSV",
  "分号 ;": "Semicolon ;",
  "竖线 |": "Pipe |",
  "上传到本机": "Load file locally",
  "尚未上传文件。所有字段先作为文本读取，保留编号前导零。": "No file loaded. All fields are read as text to preserve leading zeros in identifiers.",
  "尚未上传文件。": "No file loaded.",
  "设置清洗规则": "Configure cleaning rules",
  "条件组合": "Combine conditions",
  "满足全部条件 AND": "Match all conditions (AND)",
  "满足任意条件 OR": "Match any condition (OR)",
  "＋ 添加筛选条件": "+ Add a filter",
  "等于/不等于按文本精确比较；大于/小于按数值比较。空值不参与普通比较，请使用“为空/非空”。不设置条件则保留全部行。": "Equals / not equals compare exact text; greater / less than compare numbers. Missing values do not match ordinary comparisons; use is missing / is not missing. With no filters, all rows are retained.",
  "去除字段首尾空格": "Trim leading and trailing whitespace",
  "空值标记（每行一个）": "Missing-value markers (one per line)",
  "空字符串始终视为空值。处理顺序：去空格 → 筛选 → 删除指定列含空值的行 → 去重（保留首行）→ 固定值填补 → 输出列选择。": "Empty strings always count as missing. Order: trim → filter → drop rows missing selected values → deduplicate (keep first) → fill with constants → select output columns.",
  "列名": "Column",
  "输出": "Keep",
  "空值删行": "Drop if missing",
  "去重键": "Deduplication key",
  "填补空值": "Fill missing",
  "填充值": "Fill value",
  "固定值（保持文本）": "Constant value (as text)",
  "清空会话数据": "Clear session data",
  "每次处理都从原始上传内容重新开始。": "Each run starts from the original uploaded data.",
  "数据预览": "Data preview",
  "下载处理结果 CSV": "Download cleaned CSV",
  "Local Filter · Python 后端 + 浏览器前端 · 无第三方依赖 · 单用户会话": "Local Filter · Python backend + browser frontend · No third-party dependencies · Single-user session",
  "等于（文本）": "Equals (text)",
  "不等于（文本）": "Not equals (text)",
  "包含文本": "Contains text",
  "不包含文本": "Does not contain text",
  "大于（数值）": "Greater than (number)",
  "大于等于（数值）": "Greater than or equal (number)",
  "小于（数值）": "Less than (number)",
  "小于等于（数值）": "Less than or equal (number)",
  "为空": "Is missing",
  "非空": "Is not missing",
  "删除": "Remove",
  "筛选列": "Filter column",
  "比较方式": "Comparison",
  "条件值": "Filter value",
  "处理结果": "Cleaned results",
  "原始文件预览": "Original file preview",
  "原始行数": "Input rows",
  "筛选移除": "Filtered out",
  "空值移除": "Missing rows removed",
  "重复移除": "Duplicates removed",
  "填补单元格": "Cells filled",
  "结果行数": "Output rows",
  "请求失败": "Request failed.",
  "请选择 CSV 或 TSV 文件。": "Please choose a CSV or TSV file.",
  "文件超过 50 MB。": "The file exceeds 50 MB.",
  "正在本地读取文件…": "Reading the file locally…",
  "文件已加载，请设置条件。": "File loaded. Configure your cleaning rules.",
  "正在本地处理…": "Processing locally…",
  "处理完成。结果可在下方浏览和下载。": "Processing complete. Preview and download your results below.",
  "已发起下载，请保存在未启用云同步的本地文件夹。": "Download requested. Save the file in a local folder without cloud sync.",
  "会话数据已清空。": "Session data cleared.",
  "无法连接本地后端。": "Cannot connect to the local backend.",
  "不支持的编码或分隔符。": "Unsupported encoding or delimiter.",
  "列名不能为空或重复，请先修正表头。": "Column names must be nonempty and unique. Please fix the header.",
  "最多支持 1000 列。": "A maximum of 1,000 columns is supported.",
  "简易版最多支持 200,000 行，请先分割文件。": "This version supports up to 200,000 rows. Split the file first.",
  "文件编码不匹配，请切换编码后重新上传。": "The file encoding does not match. Choose another encoding and reload.",
  "CSV 格式有误，或单个字段超过 2 MB。": "Invalid CSV format, or a field exceeds 2 MB.",
  "请至少选择一列有效的输出列。": "Select at least one valid output column.",
  "筛选组合方式无效。": "Invalid filter combination mode.",
  "空值标记必须是文本。": "Missing-value markers must be text.",
  "筛选条件无效。": "Invalid filter condition.",
  "数值比较需要填写有效数字。": "Enter a valid number for a numeric comparison.",
  "空值处理或去重列无效。": "Invalid missing-value or deduplication columns.",
  "仅允许本地访问。": "Only local access is allowed.",
  "本地会话验证失败，请刷新。": "Local session verification failed. Reload the page.",
  "尚无结果。": "No results available yet.",
  "文件最多 50 MB。": "The maximum file size is 50 MB.",
  "请先上传文件。": "Load a file first.",
  "处理配置格式无效。": "Invalid processing configuration format.",
  "未知请求。": "Unknown request.",
  "配置格式无效。": "Invalid configuration format.",
  "内存不足，请减小文件。": "Not enough memory. Use a smaller file."
};

// UI-only translations. Never translate uploaded data or user-entered values.
let language = 'en';
const t = key => language === 'en' ? (translations[key] || key) : key;
function translatedNode(node, key) {
  node.dataset.i18n = key;
  node.textContent = t(key);
  return node;
}
function translatedAttribute(node, attr, key, prefix='') {
  node.dataset[attr === 'aria-label' ? 'i18nAria' : 'i18nPlaceholder'] = JSON.stringify([prefix,key]);
  node.setAttribute(attr, prefix + t(key));
}
function translateStatic() {
  document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
  document.querySelectorAll('[data-i18n]').forEach(n => n.textContent = t(n.dataset.i18n));
  for (const [selector, attr, data] of [['[data-i18n-aria]','aria-label','i18nAria'],['[data-i18n-placeholder]','placeholder','i18nPlaceholder']]) {
    document.querySelectorAll(selector).forEach(n => {const [prefix,key]=JSON.parse(n.dataset[data]);n.setAttribute(attr,prefix+t(key));});
  }
  document.getElementById('lang-en').setAttribute('aria-pressed',String(language === 'en'));
  document.getElementById('lang-zh').setAttribute('aria-pressed',String(language === 'zh'));
}
function translatedMessage(text) {
  const match = /^第 (\d+) 条记录的列数与表头不同，请检查分隔符。$/.exec(text);
  if (match && language === 'en') return `Record ${match[1]} has a different number of columns than the header. Check the delimiter.`;
  return t(text);
}
