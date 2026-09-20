"""统计分组逐层展开接口测试: 计数一致性 / 空分组 / 分页稳定 / 记录详情."""
import pytest

from app.services import station_service


@pytest.fixture
def empty_station(app):
    """测试区内无数据的监测点 (空点位分组)."""
    return station_service.create_station(
        {
            "code": "TEST-003",
            "name": "备用监测点",
            "area": "测试区",
            "address": None,
            "station_type": "ambient",
            "status": "active",
            "longitude": None,
            "latitude": None,
            "installed_at": None,
            "remark": None,
        }
    )


@pytest.fixture
def empty_area_station(app):
    """空白区内无数据的监测点 (空片区分组)."""
    return station_service.create_station(
        {
            "code": "TEST-004",
            "name": "未投运监测点",
            "area": "空白区",
            "address": None,
            "station_type": "rural",
            "status": "maintenance",
            "longitude": None,
            "latitude": None,
            "installed_at": None,
            "remark": None,
        }
    )


@pytest.fixture
def grouped_data(client, station, second_station, empty_station, empty_area_station,
                 entry_payload):
    """测试区 5 条 (PM25 x3 / SO2 x2, 09-03 空缺), 工业园区 1 条, 其余为空分组."""
    client.post(
        "/api/measurements/entries",
        json=entry_payload(
            station.id,
            measured_at="2026-09-01 10:00",
            period="daily",
            entries=[{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 900.0}],
        ),
    )
    client.post(
        "/api/measurements/entries",
        json=entry_payload(
            station.id,
            measured_at="2026-09-02 10:00",
            period="daily",
            entries=[{"pollutant": "PM25", "value": 100.0}, {"pollutant": "SO2", "value": 100.0}],
        ),
    )
    client.post(
        "/api/measurements/entries",
        json=entry_payload(
            station.id,
            measured_at="2026-09-04 10:00",
            period="daily",
            entries=[{"pollutant": "PM25", "value": 80.0}],
        ),
    )
    client.post(
        "/api/measurements/entries",
        json=entry_payload(
            second_station.id,
            measured_at="2026-09-01 10:00",
            entries=[{"pollutant": "CO", "value": 1.2}],
        ),
    )
    return {"station": station, "second_station": second_station}


def _by_key(items):
    return {item["key"]: item for item in items}


def test_area_level_totals_consistent_and_empty_groups_marked(client, grouped_data):
    body = client.get("/api/query/grouping?level=area").get_json()
    assert body["level"] == "area"
    assert body["parent"] is None
    assert body["breadcrumb"] == []
    assert body["total"] == 3

    items = _by_key(body["items"])
    assert items["测试区"]["count"] == 5
    assert items["测试区"]["station_count"] == 2
    assert items["测试区"]["children_total"] == 2
    assert items["工业园区"]["count"] == 1
    empty = items["空白区"]
    assert empty["count"] == 0
    assert empty["is_empty"] is True
    assert empty["children_total"] == 1

    # 本层合计 = 各分组计数之和 = 全部记录数
    assert body["totals"]["count"] == 6
    assert sum(item["count"] for item in body["items"]) == body["totals"]["count"]


def test_station_level_matches_parent_count(client, grouped_data):
    body = client.get("/api/query/grouping?level=station&group_area=测试区").get_json()
    assert body["parent"]["level"] == "area"
    assert body["parent"]["count"] == 5
    assert body["totals"]["count"] == body["parent"]["count"]

    items = _by_key(body["items"])
    station_id = grouped_data["station"].id
    assert items[station_id]["count"] == 5
    assert items[station_id]["children_total"] == 6  # 全部 6 个因子分组
    empty = [item for item in body["items"] if item["code"] == "TEST-003"][0]
    assert empty["count"] == 0
    assert empty["is_empty"] is True

    breadcrumb = body["breadcrumb"]
    assert [crumb["level"] for crumb in breadcrumb] == ["area"]


def test_pollutant_level_covers_all_factors_in_canonical_order(client, grouped_data):
    station_id = grouped_data["station"].id
    body = client.get(
        "/api/query/grouping?level=pollutant&group_station_id=%s" % station_id
    ).get_json()
    assert [item["key"] for item in body["items"]] == ["PM25", "PM10", "SO2", "NO2", "CO", "O3"]

    items = _by_key(body["items"])
    assert items["PM25"]["count"] == 3
    assert items["PM25"]["children_total"] == 4  # 09-01 ~ 09-04, 含空分组 09-03
    assert items["SO2"]["count"] == 2
    for code in ("PM10", "NO2", "CO", "O3"):
        assert items[code]["count"] == 0
        assert items[code]["is_empty"] is True

    assert body["parent"]["level"] == "station"
    assert body["parent"]["count"] == 5
    assert body["totals"]["count"] == body["parent"]["count"]


def test_bucket_level_marks_empty_days_instead_of_skipping(client, grouped_data):
    station_id = grouped_data["station"].id
    url = "/api/query/grouping?level=bucket&group_station_id=%s&group_pollutant=PM25" % station_id
    body = client.get(url).get_json()
    assert [item["key"] for item in body["items"]] == [
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-04",
    ]
    items = _by_key(body["items"])
    assert items["2026-09-03"]["count"] == 0
    assert items["2026-09-03"]["is_empty"] is True
    assert [items[key]["count"] for key in ("2026-09-01", "2026-09-02", "2026-09-04")] == [1, 1, 1]
    assert body["totals"]["count"] == 3
    assert body["parent"]["count"] == 3

    # 筛选起点前移后, 序列从头补齐空分组
    shifted = client.get(url + "&date_from=2026-08-31").get_json()
    assert shifted["items"][0]["key"] == "2026-08-31"
    assert shifted["items"][0]["is_empty"] is True
    assert shifted["total"] == 5

    # 按月粒度聚合到同一个时间段
    monthly = client.get(url + "&granularity=month").get_json()
    assert [(item["key"], item["count"]) for item in monthly["items"]] == [("2026-09", 3)]


def test_record_level_matches_group_count_and_stays_stable_across_pages(client, grouped_data):
    station_id = grouped_data["station"].id
    base = "/api/query/grouping?level=record&group_station_id=%s" % station_id

    bucket = client.get(base + "&group_pollutant=PM25&group_bucket=2026-09-01").get_json()
    assert bucket["total"] == 1
    assert bucket["totals"]["count"] == 1
    assert [crumb["level"] for crumb in bucket["breadcrumb"]] == [
        "station",
        "pollutant",
        "bucket",
    ]

    # 点位层直接查看记录: 计数与点位分组一致, 翻页时 totals 保持稳定
    page1 = client.get(base + "&page_size=2&page=1").get_json()
    page2 = client.get(base + "&page_size=2&page=2").get_json()
    page3 = client.get(base + "&page_size=2&page=3").get_json()
    assert page1["total"] == 5
    assert page1["totals"] == page2["totals"] == page3["totals"]
    assert page1["totals"]["count"] == 5
    ids = [item["id"] for item in page1["items"] + page2["items"] + page3["items"]]
    assert len(ids) == len(set(ids)) == 5

    # 片区层直接查看记录
    area_records = client.get(
        "/api/query/grouping?level=record&group_area=测试区"
    ).get_json()
    assert area_records["total"] == 5
    assert area_records["totals"]["count"] == 5


def test_grouping_rejects_invalid_params(client, grouped_data):
    station_id = grouped_data["station"].id
    assert client.get("/api/query/grouping?level=unknown").status_code == 422
    assert client.get("/api/query/grouping?granularity=week").status_code == 422
    assert client.get("/api/query/grouping?level=station").status_code == 422
    assert (
        client.get(
            "/api/query/grouping?level=bucket&group_station_id=%s" % station_id
        ).status_code
        == 422
    )
    assert (
        client.get("/api/query/grouping?level=record&group_pollutant=XX").status_code == 422
    )
    assert (
        client.get(
            "/api/query/grouping?level=record&group_station_id=%s&group_bucket=09-01"
            % station_id
        ).status_code
        == 422
    )


def test_measurement_detail_exposes_entry_content_and_judgement(client, station, entry_payload):
    response = client.post(
        "/api/measurements/entries",
        json=entry_payload(
            station.id,
            measured_at="2026-09-01 10:00",
            period="daily",
            entries=[{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 900.0}],
        ),
    )
    created = {item["pollutant"]: item for item in response.get_json()["created"]}

    exceeded = client.get("/api/measurements/%s" % created["SO2"]["id"]).get_json()
    assert exceeded["is_exceeded"] is True
    assert exceeded["limit_value"] == 150.0
    assert exceeded["exceed_ratio"] == 6.0
    assert exceeded["recorder"] == "测试员"
    assert exceeded["exceedance"]["status"] == "pending"
    assert exceeded["exceedance"]["level"] == "severe"

    normal = client.get("/api/measurements/%s" % created["PM25"]["id"]).get_json()
    assert normal["is_exceeded"] is False
    assert "exceedance" not in normal
