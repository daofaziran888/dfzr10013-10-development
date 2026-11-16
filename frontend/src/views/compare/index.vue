<template>
  <section class="page" data-module="compare">
    <header class="page-head">
      <div>
        <h2>同期对比分析管理</h2>
        <p class="page-desc">
          维护对比方案的指标、基准/对比周期与维度，导入带业务日期的明细后按周期对齐计算差异。
          每次计算生成不可变的数据集版本，供经营分析报告生成数据附件。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="toggleCreate">{{ showCreate ? '收起登记表单' : '登记对比方案' }}</button>
        <button class="btn" type="button" @click="exportRows">导出同期对比分析清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="showCreate" class="panel" @submit.prevent="createPlan">
      <h3 class="panel-title">登记对比方案</h3>
      <div class="form-grid">
        <label class="filter-item">
          <span>方案编号</span>
          <input v-model="createForm.code" placeholder="如 COMP-0004" />
        </label>
        <label class="filter-item">
          <span>方案名称</span>
          <input v-model="createForm.name" placeholder="如 销售额月度同期对比" />
        </label>
        <label class="filter-item">
          <span>对比指标</span>
          <select v-model="createForm.metric" multiple size="4">
            <option v-for="metric in metrics" :key="metric.指标编码" :value="metric.指标编码">
              {{ metric.指标名称 }}（{{ metric.指标类型 }}）
            </option>
          </select>
        </label>
        <label class="filter-item">
          <span>维度（可多选或手填）</span>
          <select v-model="createForm.dimension" multiple size="4">
            <option v-for="dim in dimensionOptions" :key="dim" :value="dim">{{ dim }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>基准周期开始</span>
          <input v-model="createForm.baseStart" type="date" />
        </label>
        <label class="filter-item">
          <span>基准周期结束</span>
          <input v-model="createForm.baseEnd" type="date" />
        </label>
        <label class="filter-item">
          <span>对比周期开始</span>
          <input v-model="createForm.compStart" type="date" />
        </label>
        <label class="filter-item">
          <span>对比周期结束</span>
          <input v-model="createForm.compEnd" type="date" />
        </label>
        <label class="filter-item">
          <span>对齐方式</span>
          <select v-model="createForm.alignment">
            <option value="按日对齐">按日对齐</option>
            <option value="按整期">按整期</option>
          </select>
        </label>
      </div>
      <div class="panel-actions">
        <button class="btn primary" type="submit">提交登记</button>
        <span v-if="createMessage" :class="createOk ? 'ok-text' : 'error-text'">{{ createMessage }}</span>
      </div>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>计算状态</th>
          <th>当前版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span :class="row.计算状态 === '已完成' ? 'tag ok' : (row.计算状态 === '计算失败' ? 'tag bad' : 'tag')">
              {{ row.计算状态 ?? '待计算' }}
            </span>
          </td>
          <td>{{ row.当前版本 ? `V${row.当前版本}` : '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openWorkbench(row)">明细与计算</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无同期对比分析数据，可先登记对比方案</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条同期对比分析记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="workbench" class="panel" data-panel="compare-workbench">
      <header class="panel-head">
        <div>
          <h3 class="panel-title">{{ workbench.方案编号 }} · {{ workbench.方案名称 }}</h3>
          <p class="page-desc">
            指标：{{ (workbench.指标明细 ?? []).map((m: any) => `${m.指标名称}(${m.指标类型})`).join('、') || '—' }}
            · 维度：{{ (workbench.维度列表 ?? []).join('、') }}
            · 周期：{{ workbench.基准周期 }} 对 {{ workbench.对比周期 }}
            · 明细修订号 R{{ workbench.明细修订号 ?? 0 }}
          </p>
        </div>
        <div class="panel-actions">
          <button class="btn primary" type="button" @click="calculate(workbench.id)">按明细计算</button>
          <button class="btn" type="button" @click="loadDetails(workbench.id)">刷新明细</button>
          <button class="btn ghost" type="button" @click="workbench = null">关闭</button>
        </div>
      </header>

      <div v-if="workbenchMessage" :class="workbenchOk ? 'ok-text' : 'error-text'" class="panel-message">
        {{ workbenchMessage }}
      </div>

      <div class="subsection">
        <h4>导入明细</h4>
        <p class="hint">
          每行格式：指标编码, 业务日期, 维度名=维度值(多个用分号), 指标值, 上报状态(可填 未上报), 分子, 分母。
          未上报行不要填数值；真实零值请填 0 并标记已上报。
        </p>
        <textarea v-model="importText" rows="5" placeholder="SALES_AMOUNT,2026-03-05,区域=华东,12000,已上报&#10;CONVERSION_RATE,2026-02-03,渠道=线上,5.2,已上报,52,1000"></textarea>
        <div class="panel-actions">
          <button class="btn primary" type="button" @click="importDetails">导入</button>
          <span class="hint">导入会整体校验，任一行不合法则全部不写入。</span>
        </div>
      </div>

      <div class="subsection">
        <h4>明细（{{ details.length }} 条）</h4>
        <table class="data-table">
          <thead>
            <tr><th>id</th><th>指标</th><th>业务日期</th><th>维度值</th><th>指标值</th><th>分子</th><th>分母</th><th>上报状态</th><th>来源批次</th><th>修订号</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in details" :key="String(row.id)">
              <td>{{ row.id }}</td>
              <td>{{ row.指标编码 }}</td>
              <td>{{ row.业务日期 }}</td>
              <td>{{ formatDims(row.维度值) }}</td>
              <td>{{ row.指标值 ?? '—' }}</td>
              <td>{{ row.分子值 ?? '—' }}</td>
              <td>{{ row.分母值 ?? '—' }}</td>
              <td>{{ row.上报状态 }}</td>
              <td>{{ row.来源批次 }}</td>
              <td>R{{ row.修订号 }}</td>
            </tr>
            <tr v-if="!details.length"><td colspan="10" class="empty-state">暂无明细，可先导入</td></tr>
          </tbody>
        </table>
      </div>

      <div class="subsection">
        <h4>数据集版本</h4>
        <table class="data-table">
          <thead>
            <tr><th>数据集编号</th><th>版本</th><th>状态</th><th>基准周期</th><th>对比周期</th><th>明细修订号</th><th>计算批次</th><th>生成时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in workbench.版本历史 ?? []" :key="String(item.数据集id)">
              <td>{{ item.数据集编号 }}</td>
              <td>V{{ item.版本 }}</td>
              <td>{{ item.状态 }}</td>
              <td>{{ item.基准周期 }}</td>
              <td>{{ item.对比周期 }}</td>
              <td>R{{ item.明细修订号 }}</td>
              <td>{{ item.计算批次 }}</td>
              <td>{{ item.生成时间 }}</td>
              <td class="row-actions">
                <button class="link" type="button" @click="loadDataset(item.数据集id)">查看</button>
              </td>
            </tr>
            <tr v-if="!(workbench.版本历史 ?? []).length"><td colspan="9" class="empty-state">尚无已发布数据集</td></tr>
          </tbody>
        </table>
      </div>

      <div v-if="dataset" class="subsection" data-panel="dataset-detail">
        <h4>{{ dataset.数据集编号 }} · V{{ dataset.版本 }} · {{ dataset.状态 }}</h4>
        <p class="page-desc">
          基准 {{ dataset.周期?.基准周期?.文本 }}（{{ dataset.周期?.基准周期?.自然日数 }} 天）
          对 对比 {{ dataset.周期?.对比周期?.文本 }}（{{ dataset.周期?.对比周期?.自然日数 }} 天）
          · {{ dataset.对齐方式 }} · 对齐 {{ dataset.周期?.对齐?.对齐天数 }} 天
          · 来源批次 {{ dataset.计算批次 }} · 明细修订号 R{{ dataset.明细修订号 }}
        </p>
        <div v-if="(dataset.覆盖说明 ?? []).length" class="notice">
          <p v-for="line in dataset.覆盖说明" :key="line">{{ line }}</p>
        </div>

        <div v-for="metric in dataset.指标 ?? []" :key="metric.指标编码" class="metric-block">
          <h5>
            {{ metric.指标名称 }}
            <span class="tag">{{ metric.指标类型 }}</span>
            <span :class="metric.状态 === '正常变化' ? 'tag ok' : 'tag warn'">{{ metric.状态 }}</span>
          </h5>
          <p class="page-desc">
            可比汇总：基准 {{ metric.可比汇总?.基准值 }}，对比 {{ metric.可比汇总?.对比值 }}
            <template v-if="metric.指标类型 === '比率'">
              ；分子 {{ metric.可比汇总?.基准分子 }} → {{ metric.可比汇总?.对比分子 }}，
              分母 {{ metric.可比汇总?.基准分母 }} → {{ metric.可比汇总?.对比分母 }}
            </template>
            <template v-if="metric.比较结果?.变化率 !== null && metric.比较结果?.变化率 !== undefined">
              ；变化率 {{ metric.比较结果.变化率 }}%
            </template>
            ；可比天数 {{ metric.可比天数 }}（汇总按最窄可比天数计）
          </p>
          <p class="page-desc">{{ metric.比较结果?.说明 }}</p>
          <table class="data-table">
            <thead>
              <tr>
                <th>维度</th><th>状态</th>
                <th>基准原始值</th><th>基准参与值</th><th>基准已/未上报</th>
                <th>对比原始值</th><th>对比参与值</th><th>对比已/未上报</th>
                <th>可比天数</th><th>变化率</th><th>变化量</th><th>说明</th><th>核对</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cell in metric.维度明细 ?? []" :key="cell.维度标签">
                <td>{{ cell.维度标签 }}</td>
                <td><span :class="cell.状态 === '正常变化' ? 'tag ok' : 'tag warn'">{{ cell.状态 }}</span></td>
                <td>{{ cell.基准期?.原始范围数值 }}</td>
                <td>{{ cell.基准期?.参与比较数值 }}</td>
                <td>{{ cell.基准期?.已上报条数 }} / {{ cell.基准期?.未上报条数 }}</td>
                <td>{{ cell.对比期?.原始范围数值 }}</td>
                <td>{{ cell.对比期?.参与比较数值 }}</td>
                <td>{{ cell.对比期?.已上报条数 }} / {{ cell.对比期?.未上报条数 }}</td>
                <td>{{ cell.覆盖?.可比天数 }}</td>
                <td>{{ cell.比较结果?.变化率 ?? '—' }}</td>
                <td>{{ cell.比较结果?.变化量 ?? '—' }}</td>
                <td class="cell-note">{{ cell.比较结果?.说明 }}</td>
                <td>
                  <button class="link" type="button" @click="loadTrace(dataset.id, metric.指标编码, cell.维度值)">原始明细</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="trace" class="notice" data-panel="trace">
          <h5>核对 {{ trace.指标 }} · {{ trace.维度核对?.[0]?.维度标签 }}</h5>
          <p class="page-desc">
            数据集 {{ trace.数据集编号 }} V{{ trace.版本 }} · 批次 {{ trace.计算批次 }} ·
            汇总 {{ JSON.stringify(trace.指标汇总?.可比汇总) }}
          </p>
          <p class="page-desc">{{ trace.明细还原说明 }}</p>
          <table class="data-table">
            <thead>
              <tr><th>明细id</th><th>业务日期</th><th>维度值</th><th>指标值</th><th>分子</th><th>分母</th><th>上报状态</th><th>来源批次</th><th>修订号</th><th>当前值</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in trace.维度核对?.[0]?.原始明细 ?? []" :key="row.id">
                <td>{{ row.id }}</td><td>{{ row.业务日期 }}</td><td>{{ formatDims(row.维度值) }}</td>
                <td>{{ row.指标值 ?? '—' }}</td><td>{{ row.分子值 ?? '—' }}</td><td>{{ row.分母值 ?? '—' }}</td>
                <td>{{ row.上报状态 }}</td><td>{{ row.来源批次 }}</td><td>R{{ row.修订号 }}</td>
                <td class="cell-note">
                  <template v-if="row.已按数据集版本还原">
                    已更正：{{ row.当前值?.指标值 ?? '—' }} / {{ row.当前值?.上报状态 }}（R{{ row.当前值?.修订号 }}）
                  </template>
                  <template v-else>—</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/compare'
const columns = ['方案编号', '对比指标', '基准周期', '对比周期', '变化幅度', '变化方向', '原因分析', '方案状态']
const actions = ['执行对比', '原因分析', '归档方案']
const statuses = ['草稿', '已对比', '已分析', '已归档']
const dimensionOptions = ['区域', '渠道', '产品线', '部门']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const metrics = ref<Row[]>([])
const showCreate = ref(false)
const createMessage = ref('')
const createOk = ref(false)
const createForm = ref({
  code: '',
  name: '',
  metric: ['SALES_AMOUNT'],
  dimension: ['区域'],
  baseStart: '2026-04-01',
  baseEnd: '2026-04-30',
  compStart: '2026-05-01',
  compEnd: '2026-05-31',
  alignment: '按日对齐',
})

const workbench = ref<Row | null>(null)
const workbenchMessage = ref('')
const workbenchOk = ref(false)
const details = ref<Row[]>([])
const dataset = ref<Row | null>(null)
const trace = ref<Row | null>(null)
const importText = ref('')

const stats = ref([
  { label: '对比方案总数', value: 0 },
  { label: '已生成数据集', value: 0 },
  { label: '缺报/不可比维度', value: 0 },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function toggleCreate() {
  showCreate.value = !showCreate.value
}

function formatDims(value: unknown) {
  if (!value || typeof value !== 'object') return '—'
  return Object.entries(value as Record<string, string>).map(([key, val]) => `${key}=${val}`).join(' / ')
}

async function createPlan() {
  createMessage.value = ''
  if (!createForm.value.code.trim()) {
    createOk.value = false
    createMessage.value = '请填写方案编号'
    return
  }
  if (!createForm.value.metric.length || !createForm.value.dimension.length) {
    createOk.value = false
    createMessage.value = '请至少选择 1 个指标和 1 个维度'
    return
  }
  const body = {
    values: {
      方案编号: createForm.value.code.trim(),
      方案名称: createForm.value.name.trim(),
      指标列表: createForm.value.metric,
      维度列表: createForm.value.dimension,
      基准周期开始: createForm.value.baseStart,
      基准周期结束: createForm.value.baseEnd,
      对比周期开始: createForm.value.compStart,
      对比周期结束: createForm.value.compEnd,
      对齐方式: createForm.value.alignment,
    },
  }
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify(body) })
    const payload = await response.json()
    createOk.value = Boolean(payload.ok)
    createMessage.value = payload.message ?? ''
    if (payload.ok) {
      createForm.value.code = ''
      createForm.value.name = ''
      await reload()
    }
  } catch (error) {
    createOk.value = false
    createMessage.value = error instanceof Error ? error.message : '方案登记失败'
  }
}

async function openWorkbench(row: Row) {
  workbenchMessage.value = ''
  dataset.value = null
  trace.value = null
  importText.value = ''
  try {
    workbench.value = await fetchJson<Row>(`${ENDPOINT}/${row.id}`)
    await loadDetails(row.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '方案明细读取失败'
  }
}

async function loadDetails(planId: number) {
  const payload = await fetchJson<{ items: Row[]; total: number }>(`${ENDPOINT}/${planId}/details?size=500`)
  details.value = payload.items ?? []
}

function parseImportText(): Row[] {
  const items: Row[] = []
  for (const raw of importText.value.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const [metric, day, dims, value, status, numerator, denominator] = line.split(',').map((part) => part.trim())
    const dimValues: Record<string, string> = {}
    for (const pair of dims.split(';')) {
      const [name, dimValue] = pair.split('=').map((part) => part.trim())
      if (name && dimValue) dimValues[name] = dimValue
    }
    const item: Row = { 指标编码: metric, 业务日期: day, 维度值: dimValues, 上报状态: status || '已上报' }
    if (value !== undefined && value !== '') item.指标值 = Number(value)
    if (numerator !== undefined && numerator !== '') item.分子值 = Number(numerator)
    if (denominator !== undefined && denominator !== '') item.分母值 = Number(denominator)
    items.push(item)
  }
  return items
}

async function importDetails() {
  if (!workbench.value) return
  workbenchMessage.value = ''
  const items = parseImportText()
  if (!items.length) {
    workbenchOk.value = false
    workbenchMessage.value = '请至少填写一行明细'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${workbench.value.id}/details`, {
      method: 'POST',
      body: JSON.stringify({ items }),
    })
    const payload = await response.json()
    workbenchOk.value = Boolean(payload.ok)
    workbenchMessage.value = payload.message ?? ''
    if (payload.ok) {
      importText.value = ''
      workbench.value = await fetchJson<Row>(`${ENDPOINT}/${workbench.value.id}`)
      await loadDetails(workbench.value.id)
      await reload()
    }
  } catch (error) {
    workbenchOk.value = false
    workbenchMessage.value = error instanceof Error ? error.message : '明细导入失败'
  }
}

async function calculate(planId: number) {
  workbenchMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${planId}/calculate`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const payload = await response.json()
    workbenchOk.value = Boolean(payload.ok)
    workbenchMessage.value = payload.message ?? ''
    if (payload.ok && payload.entry?.数据集id) {
      workbench.value = await fetchJson<Row>(`${ENDPOINT}/${planId}`)
      await loadDataset(payload.entry.数据集id)
    }
    await reload()
  } catch (error) {
    workbenchOk.value = false
    workbenchMessage.value = error instanceof Error ? error.message : '计算失败'
  }
}

async function loadDataset(datasetId: number) {
  trace.value = null
  dataset.value = await fetchJson<Row>(`${ENDPOINT}/datasets/${datasetId}`)
  updateStats()
}

async function loadTrace(datasetId: number, metric: string, dims: Record<string, string>) {
  const key = Object.keys(dims ?? {}).sort().map((name) => `${name}=${dims[name]}`).join('|')
  const query = new URLSearchParams({ metric, dimension: key }).toString()
  trace.value = await fetchJson<Row>(`${ENDPOINT}/datasets/${datasetId}/trace?${query}`)
}

function updateStats() {
  const levels = (dataset.value?.指标 ?? []).flatMap((metric: Row) => metric.维度明细 ?? [])
  stats.value = [
    { label: '对比方案总数', value: total.value },
    { label: '当前数据集版本', value: `V${dataset.value?.版本 ?? workbench.value?.当前版本 ?? '—'}` },
    {
      label: '缺报/单侧维度',
      value: levels.filter((cell: Row) => ['缺报不可比', '新增维度', '消失维度'].includes(cell.状态)).length,
    },
  ]
}
async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message ?? '同期对比分析动作未生效')
    }
    if (workbench.value?.id === row.id) {
      workbench.value = await fetchJson<Row>(`${ENDPOINT}/${row.id}`)
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '同期对比分析操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const payload = await fetchJson<{ items: Row[]; total: number }>(`${ENDPOINT}?${query}`)
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '同期对比分析列表读取失败'
  }
}

onMounted(async () => {
  try {
    const payload = await fetchJson<{ items: Row[] }>(`${ENDPOINT}/metrics`)
    metrics.value = payload.items ?? []
  } catch {
    metrics.value = []
  }
  await reload()
})
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin: 14px 0; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.panel-title { margin: 0; font-size: 15px; }
.panel-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.panel-message { margin: 8px 0; font-size: 13px; white-space: pre-wrap; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.form-grid select, .form-grid input { width: 100%; padding: 5px 6px; border: 1px solid var(--border); border-radius: 5px; }
.subsection { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 10px; }
.subsection h4 { margin: 0 0 6px; font-size: 14px; }
.subsection textarea { width: 100%; border: 1px solid var(--border); border-radius: 5px; padding: 6px; font-family: monospace; font-size: 12px; }
.metric-block { margin-top: 14px; }
.metric-block h5 { margin: 0 0 4px; font-size: 13px; display: flex; gap: 6px; align-items: center; }
.notice { background: #f8fafc; border: 1px dashed var(--border); border-radius: 6px; padding: 8px 10px; margin: 8px 0; font-size: 12px; }
.notice p { margin: 2px 0; }
.hint { color: var(--muted); font-size: 12px; }
.cell-note { max-width: 260px; font-size: 12px; color: var(--muted); }
.tag { display: inline-block; border-radius: 4px; padding: 1px 6px; font-size: 11px; background: #eef2f7; color: #334155; }
.tag.ok { background: #e7f6ec; color: #17663a; }
.tag.warn { background: #fdf2e3; color: #8a5a10; }
.tag.bad { background: #fde8e8; color: #9b1c1c; }
.ok-text { color: #17663a; }
</style>
