from database import DEMO_CERTIFICATES, get_demo_certificates, initialize_database


def test_initialize_database_seeds_synthetic_records_once(tmp_path):
    database_path = tmp_path / "demo.sqlite3"

    initialize_database(database_path)
    initialize_database(database_path)

    records = get_demo_certificates(database_path)
    assert len(records) == len(DEMO_CERTIFICATES)
    assert [record["certificate_id"] for record in records] == sorted(
        record[0] for record in DEMO_CERTIFICATES
    )
    assert all(record["certificate_id"].startswith("DEMO-") for record in records)
