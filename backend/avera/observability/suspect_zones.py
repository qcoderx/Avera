RESOLVERS = {"payments_integration", "partner_liaison", "mobile_engineering", "core_banking"}


def suspect_zones(events, service_map):
    known = {s["id"] for s in service_map["services"]}
    observed = {e.service for e in events} & known
    zones = {service: {"component": service, "routed_to": ["payments_integration"]}
             for service in observed if service.startswith("gateway_")}
    if "core-banking" in observed:
        zones["core-banking"] = {"component": "core-banking", "routed_to": ["core_banking"]}
    for service in sorted(observed):
        if service.startswith("destination-bank-"):
            zones[f"partner_bank.{service}"] = {"component": service, "routed_to": ["partner_liaison"]}

    if "customer-app" in known and any(e.code in {"APP_RELEASED", "DEPLOY_SUCCEEDED"} for e in events):
        zones["mobile_app.release"] = {"component": "customer-app", "routed_to": ["mobile_engineering"]}
    return zones
