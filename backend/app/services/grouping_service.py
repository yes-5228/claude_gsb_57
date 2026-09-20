"""统计分组逐层展开: 片区 -> 点位 -> 因子 -> 时间段 -> 单条记录.

设计约束:
- 每一层的计数由服务端按同一组筛选条件聚合, 子层合计与父级计数严格一致;
- 空分组(有台账/有因子定义但当前筛选下无数据)显式返回 is_empty=True, 不跳过;
- 每层独立分页, 计数与 totals 基于整层计算, 翻页时保持稳定;
- 记录层可携带任意层级的分组路径, 支持从任意一层直接查看原始记录.
"""
from datetime import datetime, time, timedelta

from sqlalchemy import cast, func, or_

from ..domain.constants import STATION_STATUS_LABELS, STATION_TYPE_LABELS, label_of
from ..domain.standards import POLLUTANTS, POLLUTANT_CODES, get_pollutant
from ..errors import NotFoundError, ValidationError
from ..extensions import db
from ..models import Measurement, Station
from ..utils.pagination import page_params, paginate_query
from .query_service import apply_filters, parse_filters

LEVEL_CHOICES = ("area", "station", "pollutant", "bucket", "record")
GRANULARITY_CHOICES = ("day", "month")

# 时间段枚举上限: 超出后空分组序列过长, 要求用户缩小范围或切换粒度
MAX_DAY_BUCKETS = 366
MAX_MONTH_BUCKETS = 120


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def parse_group_path(args):
    """解析分组路径参数 (group_area / group_station_id / group_pollutant / group_bucket)."""
    station_raw = args.get("group_station_id")
    station_id = None
    if station_raw not in (None, ""):
        try:
            station_id = int(station_raw)
        except (TypeError, ValueError):
            raise ValidationError(
                "group_station_id 必须为整数", fields={"group_station_id": "invalid_integer"}
            )
    pollutant = (args.get("group_pollutant") or "").strip().upper() or None
    if pollutant and pollutant not in POLLUTANT_CODES:
        raise ValidationError(
            "未知监测因子: %s" % pollutant, fields={"group_pollutant": "unknown"}
        )
    return {
        "area": (args.get("group_area") or "").strip() or None,
        "station_id": station_id,
        "pollutant": pollutant,
        "bucket": (args.get("group_bucket") or "").strip() or None,
    }


def _require_path(level, path):
    missing = []
    if level == "station" and not path["area"]:
        missing.append("group_area")
    if level in ("pollutant", "bucket") and not path["station_id"]:
        missing.append("group_station_id")
    if level == "bucket" and not path["pollutant"]:
        missing.append("group_pollutant")
    if missing:
        raise ValidationError(
            "展开 %s 层级缺少分组路径参数: %s" % (level, ", ".join(missing)),
            fields={name: "required" for name in missing},
        )


# ---------------------------------------------------------------------------
# 分组路径约束与时间段工具
# ---------------------------------------------------------------------------

def _apply_group_path(query, path, granularity):
    """把分组路径(任意子集)作为等值约束叠加到监测数据查询上."""
    if path.get("area"):
        query = query.filter(Station.area == path["area"])
    if path.get("station_id"):
        query = query.filter(Measurement.station_id == path["station_id"])
    if path.get("pollutant"):
        query = query.filter(Measurement.pollutant == path["pollutant"])
    if path.get("bucket"):
        start, end = bucket_range(path["bucket"], granularity)
        query = query.filter(
            Measurement.measured_at >= start, Measurement.measured_at < end
        )
    return query


def bucket_range(key, granularity):
    """时间段 key -> [start, end) 监测时间范围."""
    if granularity == "day":
        try:
            day = datetime.strptime(key, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            raise ValidationError(
                "按日分组时 group_bucket 格式应为 YYYY-MM-DD",
                fields={"group_bucket": "invalid_format"},
            )
        start = datetime.combine(day, time.min)
        return start, start + timedelta(days=1)
    try:
        year_str, month_str = str(key).split("-")
        year, month = int(year_str), int(month_str)
        if not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        raise ValidationError(
            "按月分组时 group_bucket 格式应为 YYYY-MM",
            fields={"group_bucket": "invalid_format"},
        )
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def bucket_series(start, end, granularity):
    """生成 [start, end] 的连续时间段 key 序列(含空分组)."""
    if granularity == "day":
        total = (end - start).days + 1
        if total > MAX_DAY_BUCKETS:
            raise ValidationError(
                "时间跨度 %d 天超出按日分组上限 %d 天, 请缩小时间范围或改用按月分组"
                % (total, MAX_DAY_BUCKETS),
                fields={"granularity": "range_too_large"},
            )
        return [(start + timedelta(days=offset)).isoformat() for offset in range(total)]
    total = (end.year - start.year) * 12 + (end.month - start.month) + 1
    if total > MAX_MONTH_BUCKETS:
        raise ValidationError(
            "时间跨度 %d 个月超出按月分组上限 %d 个月, 请缩小时间范围"
            % (total, MAX_MONTH_BUCKETS),
            fields={"granularity": "range_too_large"},
        )
    keys = []
    year, month = start.year, start.month
    for _ in range(total):
        keys.append("%04d-%02d" % (year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return keys


def _series_bounds(filters, data_min, data_max):
    """时间段序列的起止日期: 优先取筛选范围, 否则取数据实际跨度."""
    start = filters["date_from"].date() if filters["date_from"] else None
    end = filters["date_to"].date() if filters["date_to"] else None
    if start is None and data_min is not None:
        start = data_min.date()
    if end is None and data_max is not None:
        end = data_max.date()
    return start, end


def _series_length(start, end, granularity):
    if start is None or end is None:
        return 0
    if granularity == "day":
        return (end - start).days + 1
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


# ---------------------------------------------------------------------------
# 计数聚合
# ---------------------------------------------------------------------------

def _exceeded_expr():
    return func.sum(cast(Measurement.is_exceeded, db.Integer))


def _count_map(filters, group_column, path=None, granularity="day"):
    """按某列分组统计 (count, exceeded), 键为分组列的值."""
    query = db.session.query(
        group_column, func.count(Measurement.id), _exceeded_expr()
    )
    query = apply_filters(query, filters)
    if path:
        query = _apply_group_path(query, path, granularity)
    rows = query.group_by(group_column).all()
    return {
        row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in rows
    }


def _single_count(filters, path):
    """某个分组路径下的记录总数与超标数 (用于父级计数核对)."""
    query = db.session.query(func.count(Measurement.id), _exceeded_expr())
    query = apply_filters(query, filters)
    query = _apply_group_path(query, path, "day")
    count, exceeded = query.one()
    return int(count or 0), int(exceeded or 0)


def _station_universe(filters):
    """分组骨架: 符合监测点相关筛选条件的台账集合 (决定空分组是否出现)."""
    query = db.session.query(Station)
    if filters["station_ids"]:
        query = query.filter(Station.id.in_(filters["station_ids"]))
    if filters["areas"]:
        query = query.filter(Station.area.in_(filters["areas"]))
    if filters["station_types"]:
        query = query.filter(Station.station_type.in_(filters["station_types"]))
    if filters["keyword"]:
        like = "%" + filters["keyword"] + "%"
        query = query.filter(
            or_(Station.name.like(like), Station.code.like(like), Station.address.like(like))
        )
    return query


def _exceed_rate(count, exceeded):
    return round(exceeded / count, 4) if count else 0.0


# ---------------------------------------------------------------------------
# 各层级条目
# ---------------------------------------------------------------------------

def _area_items(filters):
    universe = _station_universe(filters)
    area_rows = (
        universe.with_entities(Station.area, func.count(Station.id))
        .group_by(Station.area)
        .order_by(Station.area)
        .all()
    )
    counts = _count_map(filters, Station.area)
    items = []
    for area, station_total in area_rows:
        count, exceeded = counts.get(area, (0, 0))
        items.append(
            {
                "key": area,
                "label": area,
                "count": count,
                "exceeded_count": exceeded,
                "exceed_rate": _exceed_rate(count, exceeded),
                "is_empty": count == 0,
                "station_count": int(station_total),
                "child_level": "station",
                "children_total": int(station_total),
            }
        )
    return items


def _station_items(filters, area):
    stations = (
        _station_universe(filters)
        .filter(Station.area == area)
        .order_by(Station.code, Station.id)
        .all()
    )
    counts = _count_map(filters, Measurement.station_id, path={"area": area})
    pollutant_total = len(filters["pollutants"]) if filters["pollutants"] else len(POLLUTANTS)
    items = []
    for station in stations:
        count, exceeded = counts.get(station.id, (0, 0))
        items.append(
            {
                "key": station.id,
                "code": station.code,
                "name": station.name,
                "label": "%s %s" % (station.code, station.name),
                "station_type_label": label_of(STATION_TYPE_LABELS, station.station_type),
                "status_label": label_of(STATION_STATUS_LABELS, station.status),
                "count": count,
                "exceeded_count": exceeded,
                "exceed_rate": _exceed_rate(count, exceeded),
                "is_empty": count == 0,
                "child_level": "pollutant",
                "children_total": pollutant_total,
            }
        )
    return items


def _pollutant_items(filters, station_id, granularity):
    if db.session.get(Station, station_id) is None:
        raise NotFoundError("监测点不存在: id=%s" % station_id)
    path = {"station_id": station_id}
    query = db.session.query(
        Measurement.pollutant,
        func.count(Measurement.id),
        _exceeded_expr(),
        func.avg(Measurement.value),
        func.max(Measurement.value),
        func.min(Measurement.measured_at),
        func.max(Measurement.measured_at),
    )
    query = _apply_group_path(apply_filters(query, filters), path, granularity)
    stats = {row[0]: row for row in query.group_by(Measurement.pollutant).all()}

    allowed = set(filters["pollutants"]) if filters["pollutants"] else None
    codes = [code for code in POLLUTANTS if allowed is None or code in allowed]
    items = []
    for code in codes:
        row = stats.get(code)
        meta = get_pollutant(code) or {}
        count = int(row[1]) if row else 0
        exceeded = int(row[2] or 0) if row else 0
        data_min, data_max = (row[5], row[6]) if row else (None, None)
        start, end = _series_bounds(filters, data_min, data_max)
        items.append(
            {
                "key": code,
                "label": meta.get("label", code),
                "name": meta.get("name"),
                "unit": meta.get("unit"),
                "count": count,
                "exceeded_count": exceeded,
                "exceed_rate": _exceed_rate(count, exceeded),
                "avg_value": round(float(row[3]), 2) if row and row[3] is not None else None,
                "max_value": round(float(row[4]), 2) if row and row[4] is not None else None,
                "is_empty": count == 0,
                "child_level": "bucket",
                "children_total": _series_length(start, end, granularity),
            }
        )
    return items


def _bucket_items(filters, station_id, pollutant, granularity):
    if db.session.get(Station, station_id) is None:
        raise NotFoundError("监测点不存在: id=%s" % station_id)
    path = {"station_id": station_id, "pollutant": pollutant}
    bounds_query = db.session.query(
        func.min(Measurement.measured_at), func.max(Measurement.measured_at)
    )
    bounds_query = _apply_group_path(apply_filters(bounds_query, filters), path, granularity)
    data_min, data_max = bounds_query.one()
    start, end = _series_bounds(filters, data_min, data_max)
    if start is None or end is None:
        return []

    keys = bucket_series(start, end, granularity)
    if granularity == "day":
        bucket_col = func.date(Measurement.measured_at)
        query = db.session.query(
            bucket_col.label("bucket"), func.count(Measurement.id), _exceeded_expr()
        )
        query = _apply_group_path(apply_filters(query, filters), path, granularity)
        rows = query.group_by("bucket").all()
        counts = {str(row[0]): (int(row[1] or 0), int(row[2] or 0)) for row in rows}
    else:
        year = func.extract("year", Measurement.measured_at)
        month = func.extract("month", Measurement.measured_at)
        query = db.session.query(
            year.label("year"),
            month.label("month"),
            func.count(Measurement.id),
            _exceeded_expr(),
        )
        query = _apply_group_path(apply_filters(query, filters), path, granularity)
        rows = query.group_by("year", "month").all()
        counts = {
            "%04d-%02d" % (int(row[0]), int(row[1])): (int(row[2] or 0), int(row[3] or 0))
            for row in rows
        }

    items = []
    for key in keys:
        count, exceeded = counts.get(key, (0, 0))
        items.append(
            {
                "key": key,
                "label": key,
                "count": count,
                "exceeded_count": exceeded,
                "exceed_rate": _exceed_rate(count, exceeded),
                "is_empty": count == 0,
                "child_level": "record",
                "children_total": count,
            }
        )
    return items


def _record_payload(filters, path, granularity):
    query = db.session.query(Measurement)
    query = _apply_group_path(apply_filters(query, filters), path, granularity)
    query = query.order_by(Measurement.measured_at.desc(), Measurement.id.desc())
    payload = paginate_query(query, lambda row: row.to_dict(include_station=True))

    totals_query = db.session.query(func.count(Measurement.id), _exceeded_expr())
    totals_query = _apply_group_path(apply_filters(totals_query, filters), path, granularity)
    count, exceeded = totals_query.one()
    payload["totals"] = {"count": int(count or 0), "exceeded_count": int(exceeded or 0)}
    return payload


# ---------------------------------------------------------------------------
# 面包屑与父级计数
# ---------------------------------------------------------------------------

def _breadcrumb(path):
    crumbs = []
    if path["area"]:
        crumbs.append({"level": "area", "key": path["area"], "label": path["area"]})
    if path["station_id"]:
        station = db.session.get(Station, path["station_id"])
        if station is None:
            raise NotFoundError("监测点不存在: id=%s" % path["station_id"])
        crumbs.append(
            {
                "level": "station",
                "key": station.id,
                "label": "%s %s" % (station.code, station.name),
            }
        )
    if path["pollutant"]:
        meta = get_pollutant(path["pollutant"])
        crumbs.append(
            {
                "level": "pollutant",
                "key": path["pollutant"],
                "label": meta["label"] if meta else path["pollutant"],
            }
        )
    if path["bucket"]:
        crumbs.append({"level": "bucket", "key": path["bucket"], "label": path["bucket"]})
    return crumbs


def _parent_payload(level, filters, path):
    """父级分组计数, 供前端与本层 totals 核对一致性."""
    if level == "station":
        count, exceeded = _single_count(filters, {"area": path["area"]})
        return {
            "level": "area",
            "key": path["area"],
            "label": path["area"],
            "count": count,
            "exceeded_count": exceeded,
        }
    if level == "pollutant":
        station = db.session.get(Station, path["station_id"])
        if station is None:
            raise NotFoundError("监测点不存在: id=%s" % path["station_id"])
        count, exceeded = _single_count(filters, {"station_id": path["station_id"]})
        return {
            "level": "station",
            "key": station.id,
            "label": "%s %s" % (station.code, station.name),
            "count": count,
            "exceeded_count": exceeded,
        }
    if level == "bucket":
        count, exceeded = _single_count(
            filters, {"station_id": path["station_id"], "pollutant": path["pollutant"]}
        )
        meta = get_pollutant(path["pollutant"])
        return {
            "level": "pollutant",
            "key": path["pollutant"],
            "label": meta["label"] if meta else path["pollutant"],
            "count": count,
            "exceeded_count": exceeded,
        }
    return None


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _paginate_items(items, page, page_size):
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
        "totals": {
            "count": sum(item["count"] for item in items),
            "exceeded_count": sum(item["exceeded_count"] for item in items),
        },
    }


def grouping(args):
    level = args.get("level") or "area"
    if level not in LEVEL_CHOICES:
        raise ValidationError(
            "level 仅支持: %s" % ", ".join(LEVEL_CHOICES), fields={"level": "unknown"}
        )
    granularity = args.get("granularity") or "day"
    if granularity not in GRANULARITY_CHOICES:
        raise ValidationError(
            "granularity 仅支持: %s" % ", ".join(GRANULARITY_CHOICES),
            fields={"granularity": "unknown"},
        )
    filters = parse_filters(args)
    path = parse_group_path(args)
    _require_path(level, path)
    page, page_size = page_params()

    if level == "area":
        payload = _paginate_items(_area_items(filters), page, page_size)
    elif level == "station":
        payload = _paginate_items(_station_items(filters, path["area"]), page, page_size)
    elif level == "pollutant":
        payload = _paginate_items(
            _pollutant_items(filters, path["station_id"], granularity), page, page_size
        )
    elif level == "bucket":
        payload = _paginate_items(
            _bucket_items(filters, path["station_id"], path["pollutant"], granularity),
            page,
            page_size,
        )
    else:
        payload = _record_payload(filters, path, granularity)

    payload["level"] = level
    payload["granularity"] = granularity
    payload["path"] = path
    payload["breadcrumb"] = _breadcrumb(path)
    payload["parent"] = _parent_payload(level, filters, path)
    return payload
