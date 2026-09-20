import Modal from '../../../components/common/Modal.jsx'
import Tag from '../../../components/common/Tag.jsx'
import RecordsPanel from './RecordsPanel.jsx'

const LEVEL_NAMES = {
  area: '片区',
  station: '点位',
  pollutant: '因子',
  bucket: '时间段'
}

/** 从任意分组层级直接打开原始记录列表 (抽屉)。 */
export default function RecordsDrawer({ target, filters, granularity, onClose, onOpenRecord }) {
  return (
    <Modal open={Boolean(target)} drawer width="wide" title="分组原始记录" onClose={onClose}>
      {target ? (
        <div className="stack">
          <div className="group-breadcrumb">
            {target.breadcrumb.map((crumb, index) => (
              <span key={`${crumb.level}-${crumb.key}`}>
                {index > 0 ? <span className="muted"> / </span> : null}
                <Tag tone="outline">{LEVEL_NAMES[crumb.level] || crumb.level}</Tag>{' '}
                <span className="strong">{crumb.label}</span>
              </span>
            ))}
          </div>
          <RecordsPanel
            path={target.path}
            filters={filters}
            granularity={granularity}
            onOpenRecord={onOpenRecord}
          />
        </div>
      ) : null}
    </Modal>
  )
}
