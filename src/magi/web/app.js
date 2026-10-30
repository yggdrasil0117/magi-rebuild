const $ = id => document.getElementById(id);
let pending = null, lastReply = null, selectedPlan = null, viewTicket = 0, sending = false;

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
  pre.textContent = '你：' + turn.user_text + '\n\n' + turn.text;
  article.append(pre);
  if (turn.proposal_id) {
    const button = document.createElement('button');
    button.textContent = '确认保存此候选';
    button.onclick = async () => {
      button.disabled = true;
      try {
        const p = await api('/v1/proposals/' + turn.proposal_id + '?' + new URLSearchParams({chat_id: $('chat').value, reply_message_id: turn.reply}));
        const r = await api('/v1/proposals/' + p.id + '/confirm', {chat_id: p.chat, reply_message_id: p.reply, payload_hash: p.payload_hash});
        $('receipt').textContent = JSON.stringify(r, null, 2);
        button.textContent = '已保存 · ' + r.object_id + ' · v' + r.result_version;
        $('status').textContent = '计划已保存为 v' + r.result_version;
        exitRevision();
        await loadPlans();
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
    $('status').textContent = '历史已加载';
  } catch (e) { $('status').textContent = e.message; }
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
    $('mode').value = 'deliberations';
    $('target').value = latest.id;
    $('version').value = latest.version;
    $('budget').value = '';
    $('revisionNote').textContent = '正在修订「' + latest.title + '」v' + latest.version + '。填写执行反馈后发送；生成新候选后仍需你确认保存。';
    $('revisionNote').hidden = false;
    $('exitRevision').hidden = false;
    $('status').textContent = latest.needs_review ? '个人背景已变化，请在反馈中核对当前约束。' : '修订目标已选择，尚未发送消息或修改计划。';
    $('text').focus();
  } catch (e) { $('status').textContent = e.message; }
}

$('load').onclick = load;
$('plans').onclick = loadPlans;
$('viewVersion').onchange = () => selectPlan(selectedPlan, Number($('viewVersion').value));
$('getPlan').onclick = () => selectPlan($('planId').value.trim(), Number($('planVersion').value) || null);
$('compare').onclick = compareVersions;
$('fromVersion').onchange = $('toVersion').onchange = () => { $('comparison').hidden = true; };
$('feedback').onclick = beginFeedback;
$('exitRevision').onclick = exitRevision;
$('mode').onchange = () => { if ($('mode').value === 'chat') exitRevision(); };
$('reset').onclick = () => { if (!sending) { pending = null; $('status').textContent = '可填写新消息；既有任务与保存事实仍可从历史查询。'; } };
$('send').onclick = async () => {
  if (sending) return;
  if (!pending) {
    pending = {path: '/v1/' + $('mode').value, body: {chat_id: $('chat').value, message_id: crypto.randomUUID(), reply_message_id: crypto.randomUUID(), parent_reply_message_id: lastReply, user_text: $('text').value}};
    if ($('budget').value) pending.body.time_budget_minutes = Number($('budget').value);
    if ($('target').value) { pending.body.target_plan_id = $('target').value; pending.body.expected_version = Number($('version').value); }
  }
  sending = true;
  $('send').disabled = true;
  $('reset').disabled = true;
  $('status').textContent = '正在处理，请等待；断线后可重试相同消息。';
  try {
    const turn = await api(pending.path, pending.body);
    pending = null;
    $('text').value = '';
    await load();
    $('status').textContent = turn.status;
  } catch (e) { $('status').textContent = e.message + '；再次点击发送会重试原消息。'; }
  finally { sending = false; $('send').disabled = false; $('reset').disabled = false; }
};
