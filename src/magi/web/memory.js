// 独立的按需记忆管理面板；共享app.js的认证请求，不自动从聊天提取记忆。
const memoryUI = {item: null, pending: null, busy: false, uncertain: false};

// 面板按需打开，不把记忆表单常驻在聊天界面。
function showMemories(open) {
  $('memoriesPanel').hidden = !open;
  $('toggleMemories').setAttribute('aria-expanded', String(open));
  if (open) { showPlans(false); $('memoriesPanel').scrollIntoView({behavior: 'smooth', block: 'start'}); }
}

// 只展示有效全局记忆，数据库ID和预期版本由程序管理。
async function loadMemories() {
  try {
    const result = await api('/v1/memories');
    $('memoryList').replaceChildren();
    for (const item of result.items) {
      const card = document.createElement('article'), title = document.createElement('h3'), text = document.createElement('pre');
      title.textContent = item.key;
      text.textContent = item.value;
      const edit = document.createElement('button'), remove = document.createElement('button');
      edit.textContent = '修改';
      remove.textContent = '删除';
      edit.onclick = () => beginMemoryEdit(item);
      remove.onclick = () => previewMemory('delete', item);
      card.append(title, text, edit, remove);
      $('memoryList').append(card);
    }
    $('memoryStatus').textContent = result.items.length ? '共 ' + result.items.length + ' 条有效记忆。' : '还没有个人记忆。你可以主动添加需要长期使用的背景或偏好。';
  } catch (e) { $('memoryStatus').textContent = e.message; }
}

// 传入已有记录即编辑，否则新建；编辑名称保持不变，ID和版本由记录携带。
function beginMemoryEdit(item = null) {
  if (memoryUI.busy || memoryUI.pending) { $('memoryStatus').textContent = '请先确认或取消当前操作。'; return; }
  memoryUI.item = item;
  $('memoryEditorTitle').textContent = item ? '修改记忆' : '添加记忆';
  $('memoryKey').value = item ? item.key : '';
  $('memoryKey').disabled = Boolean(item);
  $('memoryValue').value = item ? item.value : '';
  $('memoryEditor').hidden = false;
  $('memoryKey').focus();
  if (item) $('memoryValue').focus();
}

// 冻结本次预览与请求ID；网络失败后重试相同内容，不偷偷提交新编辑值。
function previewMemory(action, item = memoryUI.item) {
  if (memoryUI.busy || memoryUI.pending) { $('memoryStatus').textContent = '请先确认或取消当前操作。'; return; }
  const deleting = action === 'delete';
  const key = item ? item.key : $('memoryKey').value.trim();
  const value = deleting ? null : $('memoryValue').value.trim();
  if (!deleting && (!key || !value)) { $('memoryStatus').textContent = '请填写名称和内容。'; return; }
  const body = {request_id: crypto.randomUUID(), confirmed: true};
  if (!deleting) body.value = value;
  if (item) body.expected_version = item.version;
  else body.key = key;
  const path = item ? '/v1/memories/' + encodeURIComponent(item.id) + (deleting ? '/delete' : '/update') : '/v1/memories';
  memoryUI.pending = {path, body};
  memoryUI.uncertain = false;
  $('memoryPreview').textContent = deleting ? '删除「' + key + '」\n\n' + item.value :
    (item ? '修改「' + key + '」\n\n原内容：\n' + item.value + '\n\n新内容：\n' : '添加「' + key + '」\n\n') + value;
  $('confirmMemory').textContent = deleting ? '确认删除' : '确认保存';
  $('memoryEditor').hidden = true;
  $('memoryReview').hidden = false;
  $('memoryReview').scrollIntoView({behavior: 'smooth', block: 'center'});
}

// 取消未发送预览不会写库；失败后放弃重试不等于撤销服务端已完成的操作。
function cancelMemory() {
  if (memoryUI.busy) return;
  const uncertain = memoryUI.uncertain;
  memoryUI.pending = null;
  memoryUI.item = null;
  memoryUI.uncertain = false;
  $('memoryEditor').hidden = true;
  $('memoryReview').hidden = true;
  $('memoryStatus').textContent = uncertain ? '已退出此操作；请刷新列表核对保存状态，已提交的修改不会撤销。' : '已取消，未修改记忆。';
}

// 只有这个确认按钮发起写请求；失败保留冻结请求，后台仍检查幂等与版本冲突。
async function confirmMemory() {
  if (memoryUI.busy || !memoryUI.pending) return;
  memoryUI.busy = true;
  $('confirmMemory').disabled = true;
  $('cancelMemoryReview').disabled = true;
  try {
    const result = await api(memoryUI.pending.path, memoryUI.pending.body);
    memoryUI.pending = null;
    memoryUI.item = null;
    memoryUI.uncertain = false;
    $('memoryReview').hidden = true;
    await loadMemories();
    $('memoryStatus').textContent += result.receipt.duplicate ? ' 原请求已处理，列表显示当前有效状态。' : ' 更改已保存，后续交流将重新加载有效记忆。';
    $('status').textContent = '个人记忆已更新，未保存的旧候选请重新生成。';
  } catch (e) {
    memoryUI.uncertain = true;
    $('memoryStatus').textContent = e.message + '；可重试原操作，或取消后刷新核对。版本冲突时请重新选择最新记忆。';
  } finally { memoryUI.busy = false; $('confirmMemory').disabled = false; $('cancelMemoryReview').disabled = false; }
}

$('toggleMemories').onclick = async () => {
  if (!connected) { $('settings').open = true; $('status').textContent = '请先连接本机服务。'; $('token').focus(); return; }
  const open = $('memoriesPanel').hidden;
  showMemories(open);
  if (open) await loadMemories();
};
$('closeMemories').onclick = () => { showMemories(false); $('text').focus(); };
$('refreshMemories').onclick = loadMemories;
$('newMemory').onclick = () => beginMemoryEdit();
$('previewMemory').onclick = () => previewMemory(memoryUI.item ? 'update' : 'add');
$('confirmMemory').onclick = confirmMemory;
$('cancelMemoryEdit').onclick = $('cancelMemoryReview').onclick = cancelMemory;
