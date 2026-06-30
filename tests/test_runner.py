import pytest
from unittest.mock import MagicMock, patch
import os
import sys

# Ajuster le chemin d'importation pour accéder aux modules du projet
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runner import HEALTH_DATA, app
import runner

@pytest.fixture(autouse=True)
def reset_health_data():
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
    })

def test_health_check_endpoint():
    """
    Vérifie que le endpoint /health renvoie un statut 200 OK
    et les métriques correctes lorsque le bot est en cours d'exécution.
    """
    app.config["TESTING"] = True
    client = app.test_client()
    
    # Configurer des métriques fictives saines
    HEALTH_DATA["status"] = "running"
    HEALTH_DATA["rpc_connected"] = True
    HEALTH_DATA["last_block_analyzed"] = 8453000
    HEALTH_DATA["errors_since_success"] = 0
    HEALTH_DATA["simulation_mode"] = True
    HEALTH_DATA["last_decision"] = "HOLD"
    
    response = client.get("/health")
    assert response.status_code == 200
    
    data = response.get_json()
    assert data["status"] == "running"
    assert data["rpc_connected"] is True
    assert data["last_block_analyzed"] == 8453000
    assert data["errors_since_success"] == 0
    assert data["simulation_mode"] is True
    assert data["last_decision"] == "HOLD"

def test_health_check_endpoint_unhealthy():
    """
    Vérifie que le endpoint /health renvoie 500 Internal Server Error
    lorsque le bot a accumulé trop d'erreurs consécutives (panne RPC/EVM).
    """
    app.config["TESTING"] = True
    client = app.test_client()
    
    # Forcer un état en erreur critique
    HEALTH_DATA["errors_since_success"] = 4
    
    response = client.get("/health")
    assert response.status_code == 500

@patch("runner.send_alert")
@patch("time.sleep", return_value=None)
@patch("runner.check_depeg_and_execute_strategy")
@patch("runner.RPCManager")
def test_runner_main_loop_circuit_breaker(mock_rpc_mgr_class, mock_check_strategy, mock_sleep, mock_send_alert):
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
    
    # Intercepter le sommeil pour casser la boucle infinie après le premier cycle
    call_count = 0
    def sleep_side_effect(seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Vérifier l'état du circuit breaker avant d'interrompre
            assert HEALTH_DATA["errors_since_success"] == 1
            assert HEALTH_DATA["status"] == "circuit_breaker_active"
            assert HEALTH_DATA["last_check_status"] == "error"
            # Lever une KeyboardInterrupt pour sortir de la boucle infinie de main()
            raise KeyboardInterrupt()
            
    mock_sleep.side_effect = sleep_side_effect
    
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
