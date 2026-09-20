"""分层下钻统计: 片区 → 点位 → 因子 → 时间段 → 单条记录.

核心约束:
- 每一层节点的记录数 (total_count) 与其下所有子分组条数之和 (children_count) 必须一致,
  计数由独立的聚合语句计算, 与分页 LIMIT 解耦, 翻页期间保持稳定。
- 空分组不会被跳过: 各层按"全集"枚举 (全部片区 / 全部点位 / 全部因子 / 连续时间桶),
  没有命中数据的分组以 is_empty=true、count=0 显式返回。
- 记录层复用统一的条件检索, 支持从任意一层携带路径条件直接打开原始记录。
"""
import calendar
from datetime import datetime, timedelta

from sqlalchemy import cast, func

from ..domain.constants import (
    PERIOD_LABELS,
    STATION_STATUS_LABELS,
    STATION_TYPE_LABELS,
    label_of,
)
from ..domain.standards import POLLUTANT_CODES, get_pollutant
from ..errors import NotFoundError, ValidationError
from ..extensions import db
from ..models import Measurement, Station
from ..utils.pagination import page_params
from .query_service import apply_filters, apply_sort, parse_filters

LEVELS = ("area", "station", "pollutant", "bucket", "record")
GRANULARITIES = ("day", "month")
GRANULARITY_LABELS = {"day": "按日", "month": "按月"}


# --------------------------------------------------------------------------- #
# 路径解析
# --------------------------------------------------------------------------- #
def _parse_granularity(args):
    granularity = (args.get("granularity") or "day").strip()
    if granularity not in GRANULARITIES:
        raise ValidationError(
            "granularity 仅支持: %s" % ", ".join(GRANULARITIES),
            fields={"granularity": "unknown"},
        )
    return granularity


def _parse_bucket(raw, granularity):
    """把 bucket key 解析为覆盖该时间桶的 [起, 止] 时间."""
    raw = (raw or "").strip()
    try:
        if granularity == "day":
            start = datetime.strptime(raw, "%Y-%m-%d")
            end = start + timedelta(days=1) - timedelta(microseconds=1)
        else:
            year, month = (int(part) for part in raw.split("-"))
            if not 1 <= month <= 12:
                raise ValueError
            last_day = calendar.monthrange(year, month)[1]
            start = datetime(year, month, 1)
            end = datetime(year, month, last_day, 23, 59, 59, 999999)
    except (ValueError, TypeError):
        raise ValidationError(
            "bucket 格式不正确 (%s 需要 %s)"
            % (raw, "YYYY-MM-DD" if granularity == "day" else "YYYY-MM"),
            fields={"bucket": "invalid"},
        )
    return start, end


def _resolve_path(args):
    """解析下钻路径, 并把路径条件叠加到基础筛选上 (路径优先于同名基础筛选)."""
    filters = parse_filters(args)
    path = {"area": None, "station_id": None, "pollutant": None,
            "bucket": None, "granularity": _parse_granularity(args)}

    area = (args.get("area") or "").strip()
    if area:
        path["area"] = area
        filters["areas"] = [area]

    raw_station = (args.get("station_id") or "").strip()
    if raw_station:
        try:
            station_id = int(raw_station)
        except ValueError:
            raise ValidationError("station_id 必须为整数", fields={"station_id": "invalid_integer"})
        station = db.session.get(Station, station_id)
        if station is None:
            raise NotFoundError("监测点不存在: id=%s" % station_id)
        if path["area"] and station.area != path["area"]:
            raise ValidationError(
                "监测点 %s 不属于片区 %s" % (station.code, path["area"]),
                fields={"station_id": "area_mismatch"},
            )
        path["station_id"] = station_id
        path["station"] = station
        filters["station_ids"] = [station_id]

    pollutant = (args.get("pollutant_code") or "").strip().upper()
    if pollutant:
        if pollutant not in POLLUTANT_CODES:
            raise ValidationError("未知监测因子: %s" % pollutant,
                                  fields={"pollutant_code": "unknown"})
        path["pollutant"] = pollutant
        filters["pollutants"] = [pollutant]

    raw_bucket = (args.get("bucket") or "").strip()
    if raw_bucket:
        if not path["station_id"] or not path["pollutant"]:
            raise ValidationError(
                "按时间段下钻必须同时提供 station_id 与 pollutant_code",
                fields={"bucket": "missing_parent"},
            )
        start, end = _parse_bucket(raw_bucket, path["granularity"])
        path["bucket"] = raw_bucket
        filters["date_from"] = start
        filters["date_to"] = end

    return filters, path


def _enforce_level(level, path):
    """校验当前层级的父级路径是否齐全."""
    if level == "station" and not path["area"]:
        raise ValidationError("展开点位必须提供 area", fields={"area": "required"})
    if level == "pollutant" and not path["station_id"]:
        raise ValidationError("展开因子必须提供 station_id", fields={"station_id": "required"})
    if level == "bucket" and (not path["station_id"] or not path["pollutant"]):
        raise ValidationError(
            "展开时间段必须提供 station_id 与 pollutant_code",
            fields={"path": "required"},
        )


# --------------------------------------------------------------------------- #
# 计数原语
# --------------------------------------------------------------------------- #
def _total_count(filters):
    """当前筛选 + 路径下的记录总数 (跨分页稳定)."""
    query = apply_filters(db.session.query(func.count(Measurement.id)), filters)
    return int(query.scalar() or 0)


def _group_aggregate(filters, key_expression):
    """按给定表达式分组, 返回 {组键: (条数, 超标条数, 平均值)}."""
    query = apply_filters(
        db.session.query(
            key_expression.label("gkey"),
            func.count(Measurement.id).label("cnt"),
            func.coalesce(func.sum(cast(Measurement.is_exceeded, db.Integer)), 0).label("exc"),
            func.avg(Measurement.value).label("avgv"),
        ),
        filters,
    )
    result = {}
    for row in query.group_by(key_expression).all():
        result[str(row.gkey)] = (
            int(row.cnt or 0),
            int(row.exc or 0),
            round(float(row.avgv), 2) if row.avgv is not None else None,
        )
    return result


def _bucket_expression(granularity):
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        fmt = "YYYY-MM-DD" if granularity == "day" else "YYYY-MM"
        return func.to_char(Measurement.measured_at, fmt)
    if granularity == "day":
        return func.date(Measurement.measured_at)
    return func.strftime("%Y-%m", Measurement.measured_at)


def _item(key, label, stats, extra=None):
    count, exceeded, avg_value = stats
    item = {
        "key": str(key),
        "label": label,
        "count": count,
        "exceeded_count": exceeded,
        "exceed_rate": round(exceeded / count, 4) if count else 0.0,
        "avg_value": avg_value,
        "is_empty": count == 0,
    }
    if extra:
        item.update(extra)
    return item


def _payload(level, path, filters, items, total_count, children_count, page=None,
             page_size=None, bucket_universe_size=None):
    return {
        "level": level,
        "granularity": path["granularity"],
        "path": {
            "area": path["area"],
            "station_id": path["station_id"],
            "pollutant": path["pollutant"],
            "bucket": path["bucket"],
        },
        "items": items,
        "total_count": total_count,
        "children_count": children_count,
        "empty_count": sum(1 for item in items if item["is_empty"]),
        "consistent": total_count == children_count,
        "page": page,
        "page_size": page_size,
        "bucket_universe_size": bucket_universe_size,
        "pages": (
            (bucket_universe_size + page_size - 1) // page_size
            if page_size and bucket_universe_size is not None
            else None
        ),
    }


# --------------------------------------------------------------------------- #
# 各层级
# --------------------------------------------------------------------------- #
def _station_base_query(filters, area=None):
    """点位全集 (仅受点位属性类筛选影响, 不受监测数据条件影响)."""
    query = db.session.query(Station)
    if area:
        query = query.filter(Station.area == area)
    if filters["station_types"]:
        query = query.filter(Station.station_type.in_(filters["station_types"]))
    if filters["keyword"]:
        like = "%" + filters["keyword"] + "%"
        query = query.filter(
            Station.name.like(like) | Station.code.like(like) | Station.address.like(like)
        )
    return query


def drill_areas(filters, path):
    """第一层: 片区汇总 (空片区也列出)."""
    universe = [
        row[0]
        for row in _station_base_query(filters)
        .with_entities(Station.area)
        .distinct()
        .order_by(Station.area)
        .all()
    ]
    stats = _group_aggregate(filters, Station.area)
    items = [_item(area, area, stats.get(str(area), (0, 0, None))) for area in universe]
    total = _total_count(filters)
    children = sum(item["count"] for item in items)
    return _payload("area", path, filters, items, total, children)


def drill_stations(filters, path):
    """第二层: 指定片区下的点位汇总."""
    stations = (
        _station_base_query(filters, area=path["area"])
        .order_by(Station.code)
        .all()
    )
    stats = _group_aggregate(filters, Station.id)
    items = []
    for station in stations:
        extra = {
            "station_id": station.id,
            "code": station.code,
            "name": station.name,
            "status": station.status,
            "status_label": label_of(STATION_STATUS_LABELS, station.status),
            "station_type_label": label_of(STATION_TYPE_LABELS, station.station_type),
        }
        label = "%s %s" % (station.code, station.name)
        items.append(_item(station.id, label, stats.get(str(station.id), (0, 0, None)), extra))
    total = _total_count(filters)
    children = sum(item["count"] for item in items)
    return _payload("station", path, filters, items, total, children)


def drill_pollutants(filters, path):
    """第三层: 指定点位下的因子分项 (基础筛选若限定因子则按限定全集枚举)."""
    universe = filters["pollutants"] or list(POLLUTANT_CODES)
    stats = _group_aggregate(filters, Measurement.pollutant)
    items = []
    for code in universe:
        meta = get_pollutant(code)
        items.append(
            _item(
                code,
                meta["label"] if meta else code,
                stats.get(code, (0, 0, None)),
                {"pollutant": code, "unit": meta["unit"] if meta else None,
                 "period_label": None},
            )
        )
    total = _total_count(filters)
    children = sum(item["count"] for item in items)
    return _payload("pollutant", path, filters, items, total, children)


def _data_date_range(filters):
    """当前路径下数据的最早 / 最晚监测时间 (用于无显式日期筛选时补全连续时间桶)."""
    query = apply_filters(
        db.session.query(func.min(Measurement.measured_at), func.max(Measurement.measured_at)),
        filters,
    )
    return query.one()


def _iter_bucket_keys(start, end, granularity):
    current = start.date() if granularity == "day" else datetime(start.year, start.month, 1).date()
    end_date = end.date()
    while current <= end_date:
        if granularity == "day":
            yield current.strftime("%Y-%m-%d")
            current = current + timedelta(days=1)
        else:
            yield "%04d-%02d" % (current.year, current.month)
            year = current.year + (1 if current.month == 12 else 0)
            month = 1 if current.month == 12 else current.month + 1
            current = datetime(year, month, 1).date()


def drill_buckets(filters, path, page, page_size):
    """第四层: 指定点位 + 因子下的连续时间段分项, 时间桶分页."""
    granularity = path["granularity"]

    if filters["date_from"] and filters["date_to"]:
        range_start, range_end = filters["date_from"], filters["date_to"]
    else:
        first_at, last_at = _data_date_range(filters)
        if first_at is None:
            # 该路径下完全没有数据: 没有可枚举的时间轴, 返回空但计数一致
            return _payload("bucket", path, filters, [], 0, 0, page, page_size, 0)
        range_start = filters["date_from"] or first_at
        range_end = filters["date_to"] or last_at

    universe = list(_iter_bucket_keys(range_start, range_end, granularity))
    stats = _group_aggregate(filters, _bucket_expression(granularity))

    universe_size = len(universe)
    start_index = (page - 1) * page_size
    page_keys = universe[start_index:start_index + page_size]

    period = (filters["periods"][0] if len(filters["periods"]) == 1 else None)
    items = []
    for key in page_keys:
        meta = get_pollutant(path["pollutant"])
        extra = {
            "bucket": key,
            "granularity": granularity,
            "unit": meta["unit"] if meta else None,
            "period_label": PERIOD_LABELS.get(period) if period else None,
        }
        items.append(_item(key, key, stats.get(key, (0, 0, None)), extra))

    total = _total_count(filters)
    # 全集上的子项计数之和 (跨页稳定), 用于与父级计数核对
    children = sum(stats.get(key, (0, 0, None))[0] for key in universe)
    return _payload("bucket", path, filters, items, total, children, page, page_size,
                    universe_size)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def drilldown(args, level):
    if level not in LEVELS:
        raise ValidationError("level 仅支持: %s" % ", ".join(LEVELS), fields={"level": "unknown"})
    filters, path = _resolve_path(args)
    page, page_size = page_params()

    if level == "area":
        return drill_areas(filters, path)
    _enforce_level(level, path)
    if level == "station":
        return drill_stations(filters, path)
    if level == "pollutant":
        return drill_pollutants(filters, path)
    if level == "bucket":
        return drill_buckets(filters, path, page, page_size)
    # record 层: 任意层级都可携带路径直达
    return drill_records(filters, path, page, page_size)


def drill_records(filters, path, page, page_size):
    """第五层 / 任意层直达: 当前筛选 + 路径下的原始监测记录分页."""
    query = apply_sort(apply_filters(db.session.query(Measurement), filters),
                       "measured_at", "desc")
    total = query.count()
    rows = query.limit(page_size).offset((page - 1) * page_size).all()
    items = [row.to_dict(include_station=True, include_exceedance=True) for row in rows]
    return {
        "level": "record",
        "path": {
            "area": path["area"],
            "station_id": path["station_id"],
            "pollutant": path["pollutant"],
            "bucket": path["bucket"],
        },
        "items": items,
        "total_count": total,
        "children_count": total,
        "consistent": True,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
    }
