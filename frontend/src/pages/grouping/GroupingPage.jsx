import { useState } from 'react'
import { SectionCard } from '../../components/common/Card.jsx'
import { Select } from '../../components/common/FormField.jsx'
import GroupingFilters, { INITIAL_FILTERS } from './components/GroupingFilters.jsx'
import GroupingLevel from './components/GroupingLevel.jsx'
import RecordsDrawer from './components/RecordsDrawer.jsx'
import RecordDetailModal from './components/RecordDetailModal.jsx'

const GRANULARITY_OPTIONS = [
  { value: 'day', label: '按日分段' },
  { value: 'month', label: '按月分段' }
]

export default function GroupingPage() {
  const [filters, setFilters] = useState(INITIAL_FILTERS)
  const [granularity, setGranularity] = useState('day')
  const [recordsTarget, setRecordsTarget] = useState(null)
  const [recordId, setRecordId] = useState(null)

  // 筛选或时间粒度变化时重建整棵树, 并关闭可能引用旧分组的抽屉
  const applyFilters = (next) => {
    setFilters(next)
    setRecordsTarget(null)
  }
  const changeGranularity = (value) => {
    setGranularity(value)
    setRecordsTarget(null)
  }
  const treeKey = JSON.stringify([filters, granularity])

  return (
    <>
      <GroupingFilters
        value={filters}
        onSubmit={applyFilters}
        onReset={() => applyFilters(INITIAL_FILTERS)}
      />

      <SectionCard
        title="统计分组"
        hint="片区 → 点位 → 因子 → 时间段 → 记录逐层展开, 每层计数与父级核对, 空分组显式标出, 展开时按需加载"
        actions={
          <div style={{ width: 130 }}>
            <Select
              value={granularity}
              onChange={(event) => changeGranularity(event.target.value)}
              options={GRANULARITY_OPTIONS}
              aria-label="时间段粒度"
            />
          </div>
        }
      >
        <GroupingLevel
          key={treeKey}
          level="area"
          path={{}}
          filters={filters}
          granularity={granularity}
          onOpenRecords={(path, breadcrumb) => setRecordsTarget({ path, breadcrumb })}
          onOpenRecord={setRecordId}
        />
      </SectionCard>

      <RecordsDrawer
        target={recordsTarget}
        filters={filters}
        granularity={granularity}
        onClose={() => setRecordsTarget(null)}
        onOpenRecord={setRecordId}
      />
      <RecordDetailModal measurementId={recordId} onClose={() => setRecordId(null)} />
    </>
  )
}
