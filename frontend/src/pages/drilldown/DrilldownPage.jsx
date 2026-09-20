import { useCallback, useEffect, useMemo, useState } from 'react'
import { drilldown } from '../../api/drilldown.js'
import { SectionCard } from '../../components/common/Card.jsx'
import { Alert } from '../../components/common/Feedback.jsx'
import { useAsyncData } from '../../hooks/useAsyncData.js'
import { formatPercent } from '../../utils/format.js'
import QueryFilters from '../query/components/QueryFilters.jsx'
import DrilldownTable from './components/DrilldownTable.jsx'
import RecordDrawer from './components/RecordDrawer.jsx'

const INITIAL_FILTERS = {
  keyword: '',
  station_id: '',
  area: '',
  pollutant: '',
  period: '',
  is_exceeded: '',
  exceedance_status: '',
  data_source: '',
  date_from: '',
  date_to: '',
  min_value: '',
  max_value: ''
}

export default function DrilldownPage() {
  const [filters, setFilters] = useState(INITIAL_FILTERS)
  const [granularity, setGranularity] = useState('day')
  const [drawer, setDrawer] = useState({ open: false, title: '', params: {} })

  const rootLoader = useCallback(
    () => drilldown('area', { ...filters, granularity }),
    [filters, granularity]
  )
  const { data, loading, error, reload } = useAsyncData(rootLoader)

  const exceededTotal = useMemo(
    () => (data?.items ?? []).reduce((acc, item) => acc + (item.exceeded_count || 0), 0),
    [data]
  )

  // 数据量大时按需加载: 根层只取片区汇总, 子层在展开时才请求
  useEffect(() => {
    reload().catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [granularity])

  const handleOpenRecords = (path, label) => {
    setDrawer({
      open: true,
      title: label,
      params: { ...filters, granularity, ...compactPath(path) }
    })
  }

  return (
    <>
      <QueryFilters
        value={filters}
        loading={loading}
        onSubmit={(next) => setFilters(next)}
        onReset={() => setFilters(INITIAL_FILTERS)}
      />

      {error ? <Alert tone="error">{error.message}</Alert> : null}

      <SectionCard
        title="分层下钻统计"
        hint="片区 → 点位 → 因子 → 时间段 → 单条记录; 点击 ▸ 逐层展开, 空分组显式标出不跳过"
        actions={
          <>
            <div className="inline">
              <span className="hint">时间段粒度</span>
              <select
                className="select"
                style={{ width: 110 }}
                value={granularity}
                onChange={(event) => setGranularity(event.target.value)}
              >
                <option value="day">按日</option>
                <option value="month">按月</option>
              </select>
            </div>
            <button type="button" className="btn btn-sm" onClick={() => reload()} disabled={loading}>
              刷新汇总
            </button>
          </>
        }
        footer={
          data
            ? `当前范围共 ${data.total_count} 条记录, 分布在 ${data.items.length} 个片区; 子层展开时按需加载, 时间桶层每页 20 个`
            : null
        }
      >
        <div className="stat-grid" style={{ marginBottom: 14 }}>
          <div className="stat-card">
            <div className="stat-label">记录总数</div>
            <div className="stat-value">{data ? data.total_count : '-'}</div>
            <div className="stat-foot">片区层计数, 与各子项之和一致</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">片区数</div>
            <div className="stat-value">{data ? data.items.length : '-'}</div>
            <div className="stat-foot">含空片区 {data ? data.empty_count : '-'} 个</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">超标记录</div>
            <div className="stat-value danger-text">{data ? exceededTotal : '-'}</div>
            <div className="stat-foot">
              超标率 {data && data.total_count ? formatPercent(exceededTotal / data.total_count) : '-'}
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">计数核对</div>
            <div className="stat-value" style={{ color: 'var(--success)' }}>
              {data ? (data.consistent ? '✓ 一致' : '✗ 不一致') : '-'}
            </div>
            <div className="stat-foot">每层条数与上一层计数对齐</div>
          </div>
        </div>

        <DrilldownTable
          baseFilters={filters}
          granularity={granularity}
          rootData={data}
          rootLoading={loading}
          rootError={error}
          onOpenRecords={handleOpenRecords}
        />
      </SectionCard>

      <RecordDrawer
        open={drawer.open}
        title={drawer.title}
        params={drawer.params}
        onClose={() => setDrawer((current) => ({ ...current, open: false }))}
      />
    </>
  )
}

function compactPath(path) {
  const result = {}
  if (path.area) result.area = path.area
  if (path.station_id) result.station_id = path.station_id
  if (path.pollutant) result.pollutant_code = path.pollutant
  if (path.bucket) result.bucket = path.bucket
  return result
}
