const $ = id => document.getElementById(id);
let pending = null, lastReply = null, selectedPlan = null, viewTicket = 0, sending = false;
let connected = false;

// 只切换交互状态；实际请求仍使用原来的两种后端模式。
function setMode(mode) {
  $('mode').value = mode;
  const enabled = mode === 'deliberations';
  $('analyze').textContent = '三人格分析：' + (enabled ? '开启' : '关闭');
  $('analyze').setAttribute('aria-pressed', String(enabled));
  $('modeHint').textContent = enabled ? '会从三个视角分析，并给出待你确认的建议。' : '默认日常聊天。需要比较方案或形成计划时，开启三人格分析。';
  if (!enabled) exitRevision();
}

// 初次只需连接一次；不把服务凭据存入浏览器持久存储或跳过后端认证。
async function connect() {
  if (sending || pending) { $('status').textContent = '请先完成或放弃当前消息的重试。'; return; }
  if (!$('token').value.trim()) { $('settings').open = true; $('token').focus(); return; }
  $('connect').disabled = true;
  connected = await load();
  $('connect').disabled = false;
  if (connected) { $('settings').open = false; $('status').textContent = '已连接，可以开始聊天。'; $('text').focus(); }
}

// 计划按需展开，主界面不常驻版本表单。
function showPlans(open) {
  $('plansPanel').hidden = !open;
  $('togglePlans').setAttribute('aria-expanded', String(open));
  if (open) $('plansPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
}

// 所有业务接口共用当前页面凭据；读取计划和对比不会请求模型。
async function api(path, body) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: {Authorization: 'Bearer ' + $('token').value, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.code + ': ' + data.message);
  return data;
}

// 展示一轮历史；保存按钮确认的是该轮冻结候选，不根据当前输入框重新生成。
function show(turn) {
  lastReply = turn.reply;
  const article = document.createElement('article'), pre = document.createElement('pre');
  const marker = '\n待确认候选（尚未保存）\n';
  const start = turn.proposal_id ? turn.text.indexOf(marker) : -1;
  const answer = (start >= 0 ? turn.text.slice(start + marker.length) : turn.text)
    .split('\n\n候选 ID：')[0].replace(/\n调用 \d+\/4 · 耗时 [^\n]+/g, '');
  pre.textContent = '你：' + turn.user_text + '\n\n' + answer;
  article.append(pre);
  if (turn.proposal_id) {
    const details = document.createElement('details'), summary = document.createElement('summary'), full = document.createElement('pre');
    summary.textContent = '查看三人格讨论与运行详情';
    full.textContent = turn.text;
    details.append(summary, full);
    article.append(details);
  }
  if (turn.proposal_id) {
    const button = document.createElement('button');
    const proposalPath = '/v1/proposals/' + turn.proposal_id + '?' + new URLSearchParams({chat_id: $('chat').value, reply_message_id: turn.reply});
    button.textContent = '确认保存此候选';
    // 只读真实操作状态，避免刷新后把已保存的建议再次标为待保存。
    api(proposalPath).then(p => { if (p.saved) button.textContent = '已保存为 v' + p.saved.result_version + ' · 点击查看'; }).catch(() => {});
    button.onclick = async () => {
      button.disabled = true;
      try {
        const p = await api(proposalPath);
        const r = p.saved || await api('/v1/proposals/' + p.id + '/confirm', {chat_id: p.chat, reply_message_id: p.reply, payload_hash: p.payload_hash});
        $('receipt').textContent = JSON.stringify(r, null, 2);
        button.textContent = '已保存为 v' + r.result_version + ' · 再次点击可查看';
        $('status').textContent = '计划已保存为 v' + r.result_version;
        exitRevision();
        await loadPlans();
        showPlans(true);
        await selectPlan(r.object_id, r.result_version);
      } catch (e) { $('status').textContent = e.message; }
      finally { button.disabled = false; }
    };
    article.append(button);
  }
  $('history').append(article);
}

// 从业务库恢复当前会话的展示历史，不据此推断计划的最新保存状态。
async function load() {
  try {
    const turns = await api('/v1/chats/' + encodeURIComponent($('chat').value) + '/turns');
    lastReply = null;
    $('history').replaceChildren();
    turns.forEach(show);
    $('welcome').hidden = turns.length > 0;
    $('status').textContent = '历史已加载';
    return true;
  } catch (e) { $('status').textContent = '连接未完成，请检查设置中的服务凭据。' + e.message; return false; }
}

// 不把前一个计划的基准版本和预算带入下一次普通聊天。
function exitRevision() {
  $('target').value = '';
  $('version').value = '';
  $('budget').value = '';
  $('revisionNote').hidden = true;
  $('exitRevision').hidden = true;
}

// 计划列表只显示真实保存对象；没有计划时给出下一步提示。
async function loadPlans() {
  try {
    const plans = await api('/v1/plans');
    $('planList').replaceChildren();
    for (const plan of plans) {
      const item = document.createElement('li'), button = document.createElement('button');
      button.textContent = plan.title + ' · v' + plan.version + (plan.needs_review ? ' · 需复核' : '');
      button.onclick = () => selectPlan(plan.id);
      item.append(button);
      $('planList').append(item);
    }
    $('planStatus').textContent = plans.length ? '共 ' + plans.length + ' 份已保存计划' : '还没有已保存计划。先生成候选，再确认保存。';
  } catch (e) { $('planStatus').textContent = e.message; }
}

// 用服务器返回的版本目录填充选择框，不猜测版本是否连续。
function fillVersions(id, versions, selected) {
  $(id).replaceChildren();
  for (const v of versions) {
    const option = document.createElement('option');
    option.value = v.version;
    option.textContent = 'v' + v.version + ' · ' + new Date(v.created_at).toLocaleString();
    $(id).append(option);
  }
  $(id).value = selected;
}

// version为空时读最新版；指定版本时读不可变历史，并防止迟到响应覆盖新选择。
async function selectPlan(id, version = null) {
  const ticket = ++viewTicket;
  selectedPlan = null;
  $('planDetail').hidden = true;
  $('comparison').hidden = true;
  $('planStatus').textContent = '正在读取已保存版本…';
  try {
    const base = '/v1/plans/' + encodeURIComponent(id);
    const [plan, versions] = await Promise.all([api(base + (version ? '?version=' + version : '')), api(base + '/versions')]);
    if (ticket !== viewTicket) return; // 忽略较早选择的迟到响应，避免显示错计划。
    selectedPlan = plan.id;
    $('planTitle').textContent = plan.title;
    $('planMeta').textContent = '计划 ' + plan.id + ' · 正在查看 v' + plan.version + ' · 读取时最新版 v' + versions[0].version;
    $('planBody').textContent = plan.body;
    $('planWarning').hidden = !plan.needs_review;
    fillVersions('viewVersion', versions, plan.version);
    fillVersions('fromVersion', versions, versions[Math.min(1, versions.length - 1)].version);
    fillVersions('toVersion', versions, versions[0].version);
    $('compare').disabled = versions.length < 2;
    $('planDetail').hidden = false;
    $('planStatus').textContent = versions.length < 2 ? '目前只有一个版本，修订并确认后即可对比。' : '共 ' + versions.length + ' 个已保存版本';
  } catch (e) { if (ticket === viewTicket) $('planStatus').textContent = e.message; }
}

// 比较两份已保存正文；用户改变选择后不再展示旧请求的对比结果。
async function compareVersions() {
  if (!selectedPlan) return;
  const ticket = viewTicket;
  const from = $('fromVersion').value, to = $('toVersion').value;
  $('comparison').hidden = true;
  try {
    const result = await api('/v1/plans/' + encodeURIComponent(selectedPlan) + '/compare?' + new URLSearchParams({from_version: from, to_version: to}));
    if (ticket !== viewTicket || from !== $('fromVersion').value || to !== $('toVersion').value) return;
    $('beforeTitle').textContent = 'v' + result.before.version + ' · ' + result.before.title;
    $('afterTitle').textContent = 'v' + result.after.version + ' · ' + result.after.title;
    $('beforeBody').textContent = result.before.body;
    $('afterBody').textContent = result.after.body;
    $('diff').textContent = result.diff || '标题和正文相同。';
    $('comparisonStatus').textContent = result.identical ? '标题和正文相同。' : '仅比较已保存内容，不修改计划，也不代表新版本一定更好。';
    $('comparison').hidden = false;
  } catch (e) { if (ticket === viewTicket) $('planStatus').textContent = e.message; }
}

// 只准备修订目标，不发起生成或保存；后端仍负责检查提交时的版本冲突。
async function beginFeedback() {
  if (!selectedPlan) return;
  if (pending || sending) { $('status').textContent = '请先处理当前消息的重试，或放弃重试后再选择修订目标。'; return; }
  const id = selectedPlan, ticket = viewTicket;
  try {
    // 总是重新读取最新版本；旧详情页不能静默成为过时的修订基准。
    const latest = await api('/v1/plans/' + encodeURIComponent(id));
    if (ticket !== viewTicket || pending || sending) return;
    setMode('deliberations');
    $('target').value = latest.id;
    $('version').value = latest.version;
    $('budget').value = '';
    $('revisionNote').textContent = '正在修订「' + latest.title + '」v' + latest.version + '。填写执行反馈后发送；生成新候选后仍需你确认保存。';
    $('revisionNote').hidden = false;
    $('exitRevision').hidden = false;
    $('status').textContent = latest.needs_review ? '个人背景已变化，请在反馈中核对当前约束。' : '修订目标已选择，尚未发送消息或修改计划。';
    $('text').focus();
    showPlans(false);
  } catch (e) { $('status').textContent = e.message; }
}

$('load').onclick = load;
$('connect').onclick = connect;
$('token').oninput = () => { connected = false; };
$('analyze').onclick = () => { if (!pending && !sending) setMode($('mode').value === 'chat' ? 'deliberations' : 'chat'); };
$('togglePlans').onclick = async () => {
  if (!connected) { $('settings').open = true; $('status').textContent = '请先连接本机服务。'; $('token').focus(); return; }
  const open = $('plansPanel').hidden;
  showPlans(open);
  if (open) await loadPlans();
};
$('closePlans').onclick = () => { showPlans(false); $('text').focus(); };
$('plans').onclick = loadPlans;
$('viewVersion').onchange = () => selectPlan(selectedPlan, Number($('viewVersion').value));
$('getPlan').onclick = () => selectPlan($('planId').value.trim(), Number($('planVersion').value) || null);
$('compare').onclick = compareVersions;
$('fromVersion').onchange = $('toVersion').onchange = () => { $('comparison').hidden = true; };
$('feedback').onclick = beginFeedback;
$('exitRevision').onclick = () => { exitRevision(); setMode('chat'); };
$('mode').onchange = () => setMode($('mode').value);
$('reset').onclick = () => { if (!sending) { pending = null; $('reset').hidden = true; $('status').textContent = '可以修改后重新发送。已发生的处理仍可在历史中查看。'; } };
$('send').onclick = async () => {
  if (sending) return;
  if (!connected) { $('settings').open = true; $('status').textContent = '请先在设置中连接本机服务。'; $('token').focus(); return; }
  if (!pending && !$('text').value.trim()) { $('status').textContent = '先说说你的问题。'; $('text').focus(); return; }
  if (!pending) {
    pending = {path: '/v1/' + $('mode').value, body: {chat_id: $('chat').value, message_id: crypto.randomUUID(), reply_message_id: crypto.randomUUID(), parent_reply_message_id: lastReply, user_text: $('text').value}};
    if ($('budget').value) pending.body.time_budget_minutes = Number($('budget').value);
    if ($('target').value) { pending.body.target_plan_id = $('target').value; pending.body.expected_version = Number($('version').value); }
  }
  sending = true;
  $('send').disabled = true;
  $('reset').disabled = true;
  $('analyze').disabled = true;
  $('status').textContent = '正在处理，请等待；断线后可重试相同消息。';
  try {
    const turn = await api(pending.path, pending.body);
    pending = null;
    $('reset').hidden = true;
    $('text').value = '';
    await load();
    $('status').textContent = turn.status === 'COMPLETED' ? (turn.proposal_id ? '建议已生成，由你决定是否保存。' : '已回复。') : '本次分析未完整完成，请查看回复中的说明。';
  } catch (e) { $('reset').hidden = false; $('status').textContent = e.message + '；再次点击发送会重试原消息，也可取消后重新填写。'; }
  finally { sending = false; $('send').disabled = false; $('reset').disabled = false; $('analyze').disabled = false; }
};
