from app.ingestion.loader import load_incident

scenario = load_incident("data/incidents/incident_001")

print(scenario.model_dump())