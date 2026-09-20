import { useCallback } from 'react'
import { getMeasurement } from '../../../api/measurements.js'
import Modal from '../../../components/common/Modal.jsx'
import Tag from '../../../components/common/Tag.jsx'
import { Alert, ErrorState, Loading } from '../../../components/common/Feedback.jsx'
import { EXCEEDANCE_LEVEL_TONE, EXCEEDANCE_STATUS_TONE } from '../../../constants/index.js'
import { useAsyncData } from '../../../hooks/useAsyncData.js'
import { formatDateTime, formatNumber, formatRatio } from '../../../utils/format.js'

/** 单条监测记录详情: 当时的录入内容 + 超标判定结果与标注留痕。 */
export default function RecordDetailModal({ measurementId, onClose }) {
  const loader = useCallback(() => getMeasurement(measurementId), [measurementId])
  const { data, loading, error } = useAsyncData(loader, { immediate: Boolean(measurementId) })
  const exceedance = data?.exceedance

  return (
    <Modal
      open={Boolean(measurementId)}
      width="wide"
      title="原始记录详情"
      onClose={onClose}
      footer={
        <button type="button" className="btn" onClick={onClose}>
          关闭
        </button>
      }
    >
      {loading && !data ? <Loading /> : null}
      {error && !data ? <ErrorState error={error} /> : null}
      {data ? (
        <div className="stack">
          <section>
            <h4 className="group-section-title">录入内容</h4>
            <dl className="kv">
              <dt>监测点</dt>
              <dd>
                {data.station?.code} {data.station?.name}
                {data.station?.area ? <span className="muted">（{data.station.area}）</span> : null}
              </dd>
              <dt>监测时间</dt>
              <dd>{formatDateTime(data.measured_at)}</dd>
              <dt>数据周期</dt>
              <dd>{data.period_label}</dd>
              <dt>监测因子</dt>
              <dd>{data.pollutant_label}</dd>
              <dt>监测值</dt>
              <dd className={data.is_exceeded ? 'danger-text strong' : 'strong'}>
                {formatNumber(data.value)} {data.unit}
              </dd>
              <dt>数据来源</dt>
              <dd>{data.data_source_label}</dd>
              <dt>录入人</dt>
              <dd>{data.recorder || '-'}</dd>
              <dt>备注</dt>
              <dd>{data.remark || '-'}</dd>
              <dt>录入时间</dt>
              <dd>{formatDateTime(data.created_at)}</dd>
              <dt>最后更新</dt>
              <dd>{formatDateTime(data.updated_at)}</dd>
            </dl>
          </section>
          <section>
            <h4 className="group-section-title">判定结果</h4>
            {data.limit_value === null ? (
              <Alert tone="info">
                该因子在{data.period_label}周期下无国标限值, 仅记录数值, 不参与超标判定
              </Alert>
            ) : (
              <dl className="kv">
                <dt>限值</dt>
                <dd>
                  {formatNumber(data.limit_value)} {data.unit}
                </dd>
                <dt>判定</dt>
                <dd>
                  {data.is_exceeded ? <Tag tone="danger">超标</Tag> : <Tag tone="success">达标</Tag>}
                  {data.is_exceeded ? (
                    <span className="muted small">（超标 {formatRatio(data.exceed_ratio)}）</span>
                  ) : null}
                </dd>
              </dl>
            )}
            {exceedance ? (
              <dl className="kv" style={{ marginTop: 10 }}>
                <dt>标注状态</dt>
                <dd>
                  <Tag tone={EXCEEDANCE_STATUS_TONE[exceedance.status]}>{exceedance.status_label}</Tag>
                </dd>
                <dt>超标等级</dt>
                <dd>
                  <Tag tone={EXCEEDANCE_LEVEL_TONE[exceedance.level]}>{exceedance.level_label}</Tag>
                </dd>
                <dt>标注说明</dt>
                <dd>{exceedance.note || '-'}</dd>
                <dt>标注人</dt>
                <dd>{exceedance.annotator || '-'}</dd>
                <dt>标注时间</dt>
                <dd>{formatDateTime(exceedance.annotated_at)}</dd>
              </dl>
            ) : null}
          </section>
        </div>
      ) : null}
    </Modal>
  )
}
