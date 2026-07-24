from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sonar_imports_python_coverage_report():
    properties = (ROOT / 'sonar-project.properties').read_text()

    assert 'sonar.python.coverage.reportPaths=coverage.xml' in properties
    assert 'sonar.tests=tests' in properties
    assert 'sonar.test.inclusions=tests/**/*.py' in properties


def test_shared_image_keeps_apk_transport_encrypted():
    dockerfile = (ROOT / 'shared' / 'Dockerfile').read_text()

    assert 's#https://#http://#' not in dockerfile
