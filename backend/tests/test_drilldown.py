"""分层下钻统计接口测试."""
from datetime import datetime

from app.extensions import db
from app.models import Station


def _create_area(client, station, entry_payload, entries, measured_at, period="daily"):
    response = client.post(
        "/api/measurements/entries",
        json=entry_payload(station.id, measured_at=measured_at, period=period, entries=entries),
    )
    assert response.status_code == 201, response.get_json()


def _add_station(app, code, name, area):
    with app.app_context():
        station = Station(code=code, name=name, area=area, station_type="ambient",
                          status="active")
        db.session.add(station)
        db.session.commit()
        return station.id


def test_area_layer_counts_and_empty_area(app, client, station, second_station, entry_payload):
    # 片区"测试区" 2 条, "工业园区" 1 条; 另建一个无任何数据的"空片区"
    _add_station(app, "EMPTY-001", "空白监测点", "空片区")
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 900.0}],
                 "2026-09-01 10:00")
    _create_area(client, second_station, entry_payload,
                 [{"pollutant": "PM25", "value": 30.0}], "2026-09-01 11:00")

    body = client.get("/api/query/drilldown/area").get_json()
    assert body["level"] == "area"
    areas = {item["key"]: item for item in body["items"]}

    assert set(areas) == {"测试区", "工业园区", "空片区"}
    assert areas["测试区"]["count"] == 2
    assert areas["工业园区"]["count"] == 1
    # 空分组显式标出而不是跳过
    empty = areas["空片区"]
    assert empty["count"] == 0
    assert empty["is_empty"] is True

    # 子项计数之和 == 父层(全集)计数
    assert body["children_count"] == body["total_count"] == 3
    assert body["empty_count"] == 1
    assert body["consistent"] is True


def test_station_layer_matches_area_count(app, client, station, second_station, entry_payload):
    _add_station(app, "TEST-003", "备用监测点", "测试区")
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 60.0}, {"pollutant": "CO", "value": 1.2}],
                 "2026-09-01 10:00")

    body = client.get("/api/query/drilldown/station?area=测试区").get_json()
    codes = {item["code"]: item for item in body["items"]}

    # 同片区无数据点位也要出现并标记为空
    assert set(codes) == {"TEST-001", "TEST-003"}
    assert codes["TEST-001"]["count"] == 2
    assert codes["TEST-003"]["is_empty"] is True

    # 点位层条数之和 == 片区层该片区的计数
    assert body["children_count"] == body["total_count"] == 2
    assert body["path"]["area"] == "测试区"


def test_station_layer_requires_area(client):
    response = client.get("/api/query/drilldown/station")
    assert response.status_code == 422
    assert response.get_json()["error"]["fields"]["area"] == "required"


def test_pollutant_layer_enumerates_all_factors(client, station, entry_payload):
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 900.0}],
                 "2026-09-01 10:00")

    body = client.get(f"/api/query/drilldown/pollutant?station_id={station.id}").get_json()
    factors = {item["pollutant"]: item for item in body["items"]}

    # 六种因子全部枚举, 未录入的四种标记为空
    assert set(factors) == {"PM25", "PM10", "SO2", "NO2", "CO", "O3"}
    assert factors["PM25"]["count"] == 1
    assert factors["NO2"]["is_empty"] is True
    assert factors["NO2"]["count"] == 0
    assert body["children_count"] == body["total_count"] == 2

    # 基础筛选限定单因子时, 全集收窄为该因子
    narrowed = client.get(
        f"/api/query/drilldown/pollutant?station_id={station.id}&pollutant=SO2"
    ).get_json()
    assert [item["pollutant"] for item in narrowed["items"]] == ["SO2"]
    assert narrowed["total_count"] == 1


def test_bucket_layer_fills_gaps_and_counts_stable(client, station, entry_payload):
    # 9-01 与 9-03 各一条, 中间 9-02 为空应补齐
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 60.0}], "2026-09-01 10:00")
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 80.0}], "2026-09-03 10:00")

    url = f"/api/query/drilldown/bucket?station_id={station.id}&pollutant_code=PM25"
    body = client.get(url + "&page=1&page_size=2").get_json()

    assert body["bucket_universe_size"] == 3
    assert [item["bucket"] for item in body["items"]] == ["2026-09-01", "2026-09-02"]
    middle = body["items"][1]
    assert middle["is_empty"] is True
    assert middle["count"] == 0

    # 第 1 页只返回 2 个桶, 但全集子项计数仍为 2, 与父层(因子)计数一致
    assert body["total_count"] == 2
    assert body["children_count"] == 2
    assert body["consistent"] is True
    assert body["pages"] == 2

    page2 = client.get(url + "&page=2&page_size=2").get_json()
    assert [item["bucket"] for item in page2["items"]] == ["2026-09-03"]
    # 翻页后同一层级计数保持稳定
    assert page2["total_count"] == 2
    assert page2["children_count"] == 2


def test_bucket_month_granularity(client, station, entry_payload):
    _create_area(client, station, entry_payload,
                 [{"pollutant": "NO2", "value": 250.0}], "2026-08-15 10:00")
    _create_area(client, station, entry_payload,
                 [{"pollutant": "NO2", "value": 90.0}], "2026-09-10 10:00")

    body = client.get(
        f"/api/query/drilldown/bucket?station_id={station.id}"
        "&pollutant_code=NO2&granularity=month"
    ).get_json()
    assert [item["bucket"] for item in body["items"]] == ["2026-08", "2026-09"]
    assert body["children_count"] == body["total_count"] == 2


def test_bucket_with_explicit_date_range_pads_empty_days(client, station, entry_payload):
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 60.0}], "2026-09-02 10:00")
    body = client.get(
        f"/api/query/drilldown/bucket?station_id={station.id}&pollutant_code=PM25"
        "&date_from=2026-09-01&date_to=2026-09-04"
    ).get_json()
    assert body["bucket_universe_size"] == 4
    empty_days = [item["bucket"] for item in body["items"] if item["is_empty"]]
    assert empty_days == ["2026-09-01", "2026-09-03", "2026-09-04"]
    assert body["children_count"] == 1


def test_record_layer_from_each_path_level(client, station, second_station, entry_payload):
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 900.0}],
                 "2026-09-01 10:00")
    _create_area(client, second_station, entry_payload,
                 [{"pollutant": "PM25", "value": 30.0}], "2026-09-01 11:00")

    # 从片区层直达: 计数与片区汇总一致
    by_area = client.get("/api/query/drilldown/record?area=测试区").get_json()
    assert by_area["total_count"] == 2
    assert len(by_area["items"]) == 2

    # 从点位 + 因子 + 时间桶直达: 只剩单条
    by_bucket = client.get(
        f"/api/query/drilldown/record?area=测试区&station_id={station.id}"
        "&pollutant_code=SO2&bucket=2026-09-01"
    ).get_json()
    assert by_bucket["total_count"] == 1
    row = by_bucket["items"][0]
    assert row["pollutant"] == "SO2"
    # 记录携带当时的录入内容与判定结果
    assert row["value"] == 900.0
    assert row["limit_value"] == 150.0
    assert row["is_exceeded"] is True
    assert row["exceedance"]["level_label"] in {"轻度超标", "中度超标", "重度超标"}
    assert row["exceedance"]["status"] == "pending"
    assert row["station"]["code"] == "TEST-001"
    assert row["recorder"] == "测试员"


def test_record_layer_pagination_count_stable(client, station, entry_payload):
    for day in range(1, 6):
        _create_area(client, station, entry_payload,
                     [{"pollutant": "PM25", "value": 60.0}],
                     "2026-09-%02d 10:00" % day)
    url = f"/api/query/drilldown/record?station_id={station.id}&page_size=2"
    first = client.get(url + "&page=1").get_json()
    second = client.get(url + "&page=2").get_json()
    assert first["total_count"] == second["total_count"] == 5
    assert len(first["items"]) == 2
    assert len(second["items"]) == 2


def test_full_chain_counts_are_consistent(client, station, entry_payload):
    """端到端: 片区 → 点位 → 因子 → 时间桶 → 记录, 逐层计数必须对齐."""
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 900.0}],
                 "2026-09-01 10:00")
    _create_area(client, station, entry_payload,
                 [{"pollutant": "PM25", "value": 70.0}], "2026-09-02 10:00")

    area = client.get("/api/query/drilldown/area").get_json()
    area_count = next(item["count"] for item in area["items"] if item["key"] == "测试区")

    stations = client.get("/api/query/drilldown/station?area=测试区").get_json()
    assert stations["total_count"] == area_count

    pollutants = client.get(
        f"/api/query/drilldown/pollutant?area=测试区&station_id={station.id}"
    ).get_json()
    assert pollutants["total_count"] == area_count
    pm25_count = next(item["count"] for item in pollutants["items"] if item["pollutant"] == "PM25")
    assert pm25_count == 2

    buckets = client.get(
        f"/api/query/drilldown/bucket?area=测试区&station_id={station.id}&pollutant_code=PM25"
    ).get_json()
    assert buckets["total_count"] == pm25_count
    assert buckets["children_count"] == pm25_count

    records = client.get(
        f"/api/query/drilldown/record?area=测试区&station_id={station.id}&pollutant_code=PM25"
    ).get_json()
    assert records["total_count"] == pm25_count


def test_invalid_and_cross_area_paths(client, station, second_station):
    assert client.get("/api/query/drilldown/bogus").status_code == 422
    assert client.get(
        f"/api/query/drilldown/station?area=不存在&station_id={station.id}"
    ).status_code == 422
    # second_station 属于"工业园区", 从"测试区"路径下钻应被拒绝
    response = client.get(
        f"/api/query/drilldown/pollutant?area=测试区&station_id={second_station.id}"
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["fields"]["station_id"] == "area_mismatch"


def test_bucket_requires_parent_path(client, station):
    response = client.get(
        f"/api/query/drilldown/bucket?station_id={station.id}"
    )
    assert response.status_code == 422
