import pytest
from unittest.mock import MagicMock, patch
import os
import sys

from runner import HEALTH_DATA, app, health_lock
import runner

@pytest.fixture(autouse=True)
def reset_health_data():
    with health_lock:
        HEALTH_DATA.update({
            "status": "starting",
            "rpc_connected": False,
            "last_block_analyzed": None,
            "last_check_timestamp": 0,
            "last_check_status": None,
            "errors_since_success": 0,
            "last_decision": None,
            "simulation_mode": False,
            "vault_address": None,
            "hot_wallet_address": None,
            "cold_wallet_address": None,
        })

def test_health_check_endpoint():
    """
    Vérifie que le endpoint /health renvoie un statut 200 OK
    et les métriques correctes lorsque le bot est en cours d'exécution.
    """
    app.config["TESTING"] = True
    client = app.test_client()
    
    # Configurer des métriques fictives saines
    with health_lock:
        HEALTH_DATA["status"] = "running"
        HEALTH_DATA["rpc_connected"] = True
        HEALTH_DATA["last_block_analyzed"] = 8453000
        HEALTH_DATA["errors_since_success"] = 0
        HEALTH_DATA["simulation_mode"] = True
        HEALTH_DATA["last_decision"] = "HOLD"
        HEALTH_DATA["cold_wallet_address"] = "0xColdWalletAddress"
    
    response = client.get("/health")
    assert response.status_code == 200
    
    data = response.get_json()
    assert data["status"] == "running"
    assert data["rpc_connected"] is True
    assert data["last_block_analyzed"] == 8453000
    assert data["errors_since_success"] == 0
    assert data["simulation_mode"] is True
    assert data["last_decision"] == "HOLD"
    assert data["cold_wallet_address"] == "0xColdWalletAddress"

def test_health_check_endpoint_unhealthy():
    """
    Vérifie que le endpoint /health renvoie 500 Internal Server Error
    lorsque le bot a accumulé trop d'erreurs consécutives (panne RPC/EVM).
    """
    app.config["TESTING"] = True
    client = app.test_client()
    
    # Forcer un état en erreur critique
    with health_lock:
        HEALTH_DATA["errors_since_success"] = 4
    
    response = client.get("/health")
    assert response.status_code == 500

@patch("runner.send_alert")
@patch("runner.trigger_event.wait", return_value=True)
@patch("runner.check_depeg_and_execute_strategy")
@patch("runner.RPCManager")
def test_runner_main_loop_circuit_breaker(mock_rpc_mgr_class, mock_check_strategy, mock_wait, mock_send_alert):
    """
    Teste que la boucle principale du runner intercepte les exceptions,
    active le circuit breaker (état, compteur d'erreurs), et envoie une alerte.
    """
    # Mocker RPCManager pour éviter les vrais appels réseau
    mock_rpc_inst = MagicMock()
    mock_rpc_inst.get_w3.return_value = MagicMock()
    mock_rpc_mgr_class.return_value = mock_rpc_inst
    
    # Configurer check_depeg_and_execute_strategy pour lever une exception
    mock_check_strategy.side_effect = Exception("Connexion RPC interrompue")
    
    # Intercepter l'événement d'attente pour casser la boucle infinie après le premier cycle
    call_count = 0
    def wait_side_effect(timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Vérifier l'état du circuit breaker avant d'interrompre
            with health_lock:
                assert HEALTH_DATA["errors_since_success"] == 1
                assert HEALTH_DATA["status"] == "circuit_breaker_active"
                assert HEALTH_DATA["last_check_status"] == "error"
            # Lever une KeyboardInterrupt pour sortir de la boucle infinie de main()
            raise KeyboardInterrupt()
            
    mock_wait.side_effect = wait_side_effect
    
    # Configurer temporairement l'environnement de test en mode simulation
    with patch.dict(os.environ, {"SIMULATION_MODE": "true"}):
        with pytest.raises(KeyboardInterrupt):
            runner.main()
            
    # Vérifier que les alertes ont été transmises (démarrage + panne)
    assert mock_send_alert.call_count == 2
    startup_call = mock_send_alert.call_args_list[0]
    failure_call = mock_send_alert.call_args_list[1]
    
    assert "Démarrage" in startup_call[1]["title"]
    assert "Alerte Panne" in failure_call[1]["title"]
    assert failure_call[1]["status"] == "critical"

@patch("runner.setup_logging")
@patch("runner.send_alert")
def test_runner_opsec_failure(mock_send_alert, mock_setup_logging):
    """
    Vérifie la règle OpSec : si la clé privée est absente et qu'on n'est pas
    en mode simulation, le bot doit lever une erreur et bloquer le démarrage.
    """
    with patch.dict(os.environ, {"SIMULATION_MODE": "false", "HOT_WALLET_PRIVATE_KEY": ""}):
        with pytest.raises(ValueError, match="HOT_WALLET_PRIVATE_KEY est vide"):
            runner.main()
            
    # Vérifier qu'une alerte critique a été transmise avant le crash
    mock_send_alert.assert_called_once()
    assert "critical" in mock_send_alert.call_args[1]["status"]


# --- Tests d'authentification API ---

class TestApiAuthentication:
    """Tests pour la protection par API key des endpoints sensibles."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    @patch.object(runner, "ADMIN_API_KEY", "test-secret-key-123")
    def test_protected_endpoints_return_401_without_key(self):
        """Les endpoints protégés retournent 401 sans API key."""
        protected_routes = [
            ("GET", "/api/status"),
            ("GET", "/api/logs"),
            ("POST", "/api/trigger-check"),
            ("POST", "/api/simulate-depeg"),
        ]
        
        for method, route in protected_routes:
            if method == "GET":
                response = self.client.get(route)
            else:
                response = self.client.post(route, json={})
            
            assert response.status_code == 401, f"{method} {route} should return 401, got {response.status_code}"

    @patch.object(runner, "ADMIN_API_KEY", "test-secret-key-123")
    def test_protected_endpoints_work_with_bearer_token(self):
        """Les endpoints protégés fonctionnent avec un Bearer token valide."""
        headers = {"Authorization": "Bearer test-secret-key-123"}
        
        response = self.client.get("/api/status", headers=headers)
        assert response.status_code == 200

        response = self.client.get("/api/logs", headers=headers)
        assert response.status_code == 200

    @patch.object(runner, "ADMIN_API_KEY", "test-secret-key-123")
    def test_protected_endpoints_work_with_query_param(self):
        """Les endpoints protégés fonctionnent avec le query param api_key."""
        response = self.client.get("/api/status?api_key=test-secret-key-123")
        assert response.status_code == 200

    @patch.object(runner, "ADMIN_API_KEY", "test-secret-key-123")
    def test_wrong_api_key_returns_401(self):
        """Une clé API incorrecte retourne 401."""
        headers = {"Authorization": "Bearer wrong-key"}
        response = self.client.get("/api/status", headers=headers)
        assert response.status_code == 401

    @patch.object(runner, "ADMIN_API_KEY", "")
    def test_no_api_key_configured_allows_access(self):
        """Sans ADMIN_API_KEY configurée, les endpoints sont ouverts (rétrocompatibilité)."""
        response = self.client.get("/api/status")
        assert response.status_code == 200

    def test_public_endpoints_always_accessible(self):
        """Les endpoints publics sont toujours accessibles sans API key."""
        response = self.client.get("/health")
        assert response.status_code in (200, 500)  # Dépend de l'état de HEALTH_DATA

    @patch.object(runner, "ADMIN_API_KEY", "test-secret-key-123")
    def test_simulate_depeg_blocked_in_production(self):
        """L'endpoint simulate-depeg retourne 403 en mode production."""
        headers = {"Authorization": "Bearer test-secret-key-123"}
        
        with health_lock:
            HEALTH_DATA["simulation_mode"] = False
        
        response = self.client.post(
            "/api/simulate-depeg",
            json={"enabled": True, "price": 0.95},
            headers=headers
        )
        assert response.status_code == 403
        data = response.get_json()
        assert "disabled in production" in data["error"]

    @patch.object(runner, "ADMIN_API_KEY", "test-secret-key-123")
    def test_simulate_depeg_allowed_in_simulation_mode(self):
        """L'endpoint simulate-depeg fonctionne en mode simulation."""
        headers = {"Authorization": "Bearer test-secret-key-123"}
        
        with health_lock:
            HEALTH_DATA["simulation_mode"] = True
        
        response = self.client.post(
            "/api/simulate-depeg",
            json={"enabled": True, "price": 0.95},
            headers=headers
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["simulated_depeg_active"] is True
