#!/usr/bin/env python3
"""
Moteur d'automatisation (runner.py) pour le Bot DeFi Stablecoin.
Exécute la vérification de peg en boucle fermée, gère les pannes via un circuit breaker,
et expose un endpoint /health pour UptimeRobot/Render.
"""

import os
import sys
import time
import logging
import logging.handlers
import threading
from dotenv import load_dotenv
from flask import Flask, jsonify

# Charger les variables d'environnement
load_dotenv()

# Importer les modules du bot
from defi_vault_trader import (
    RPCManager,
    check_depeg_and_execute_strategy,
    execute_with_retry
)
from alerts import send_alert

# État global partagé pour le health check
HEALTH_DATA = {
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
}

# Configuration du serveur de Health Check Flask
app = Flask("DeFiBotHealthCheck")

# Désactiver les logs de requêtes Flask par défaut pour ne pas polluer les logs de prod
log_werkzeug = logging.getLogger('werkzeug')
log_werkzeug.setLevel(logging.WARNING)

@app.route("/health", methods=["GET"])
def health_check():
    """
    Expose l'état actuel et les métriques d'exécution du bot.
    Renvoie 200 OK si le bot tourne normalement, 500 Internal Server Error si bloqué en erreur.
    """
    # Si plus de 3 erreurs consécutives sans succès, on considère le bot comme en mauvaise santé
    is_healthy = HEALTH_DATA["errors_since_success"] <= 3
    status_code = 200 if is_healthy else 500
    
    response_data = {
        "status": HEALTH_DATA["status"],
        "rpc_connected": HEALTH_DATA["rpc_connected"],
        "last_block_analyzed": HEALTH_DATA["last_block_analyzed"],
        "last_check_time_utc": time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(HEALTH_DATA["last_check_timestamp"])) if HEALTH_DATA["last_check_timestamp"] else None,
        "errors_since_success": HEALTH_DATA["errors_since_success"],
        "last_decision": HEALTH_DATA["last_decision"],
        "simulation_mode": HEALTH_DATA["simulation_mode"],
        "vault_address": HEALTH_DATA["vault_address"],
        "hot_wallet_address": HEALTH_DATA["hot_wallet_address"]
    }
    
    return jsonify(response_data), status_code

def run_health_server():
    """
    Exécute le serveur de health check sur le port configuré.
    """
    port = int(os.getenv("PORT", 8080))
    logging.info(f"[HTTP] Lancement du serveur health check sur le port {port}...")
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        logging.error(f"[HTTP] Impossible de démarrer le serveur health check : {e}")

def setup_logging():
    """
    Configure les logs de production (StreamHandler et RotatingFileHandler).
    """
    os.makedirs("logs", exist_ok=True)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Format standardisé des logs de production
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 1. Console Stream (lisible dans les logs Render)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    # 2. FileHandler tournant pour l'archivage local (max 5 Mo par fichier, 5 archives)
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    logging.info("Système de logs de production initialisé.")

def main():
    setup_logging()
    
    # Configuration des variables
    vault_address = os.getenv("DEFI_VAULT_ADDRESS", "0x83152eE78d8f20Bba134A5FF000D551355Ce3996")
    wallet_address = os.getenv("USER_WALLET_ADDRESS", "0x0000000000000000000000000000000000000000")
    hot_wallet = os.getenv("HOT_WALLET_ADDRESS") or wallet_address
    private_key = os.getenv("HOT_WALLET_PRIVATE_KEY")
    cold_wallet = os.getenv("COLD_WALLET_ADDRESS")
    
    simulation_mode = os.getenv("SIMULATION_MODE", "false").lower() in ("true", "1", "yes")
    check_interval = int(os.getenv("CHECK_INTERVAL", 60))
    circuit_breaker_sleep = int(os.getenv("CIRCUIT_BREAKER_SLEEP", 300)) # 5 minutes par défaut
    
    HEALTH_DATA["simulation_mode"] = simulation_mode
    HEALTH_DATA["vault_address"] = vault_address
    HEALTH_DATA["hot_wallet_address"] = hot_wallet
    
    logging.info("=== INITIALISATION DU BOT DEFI ===")
    logging.info(f"Mode Simulation : {simulation_mode}")
    logging.info(f"Vault de destination : {vault_address}")
    logging.info(f"Hot Wallet (Bot) : {hot_wallet}")
    logging.info(f"Cold Wallet (Safe/Ledger) : {cold_wallet or 'Non configuré (Retraits directs activés)'}")
    
    # 1. OpSec : Blocage immédiat si clé absente hors simulation
    if not private_key and not simulation_mode:
        msg = "OpSec Critical : HOT_WALLET_PRIVATE_KEY est vide en mode réel (production)."
        logging.critical(msg)
        send_alert(
            title="Échec critique du démarrage du bot",
            message=msg,
            status="critical"
        )
        raise ValueError(msg)
        
    # 2. Démarrage du thread de health check
    server_thread = threading.Thread(target=run_health_server, daemon=True)
    server_thread.start()
    
    # 3. Initialisation du gestionnaire de connexion RPC
    logging.info("Initialisation de RPCManager...")
    rpc_manager = RPCManager()
    
    send_alert(
        title="Démarrage du Bot DeFi",
        message="Le bot DeFi Stablecoin a démarré avec succès.",
        status="success",
        details={
            "Mode Simulation": "Actif" if simulation_mode else "Inactif",
            "Vault": vault_address,
            "Hot Wallet": hot_wallet
        }
    )
    
    HEALTH_DATA["status"] = "running"
    
    # 4. Boucle infinie d'exécution
    while True:
        try:
            logging.info("Exécution de la vérification périodique...")
            w3 = rpc_manager.get_w3()
            
            if w3:
                HEALTH_DATA["rpc_connected"] = True
                try:
                    HEALTH_DATA["last_block_analyzed"] = w3.eth.block_number
                except Exception as block_err:
                    logging.warning(f"Impossible d'interroger le numéro du dernier bloc : {block_err}")
            else:
                HEALTH_DATA["rpc_connected"] = False
                if not simulation_mode:
                    raise ConnectionError("Aucun RPC Web3 n'est connecté.")
            
            # Exécution de la stratégie (avec retry exponentiel et bascule RPC automatique intégrés)
            if simulation_mode:
                decision = check_depeg_and_execute_strategy(
                    None,
                    vault_address,
                    hot_wallet,
                    private_key=None,
                    cold_wallet=cold_wallet
                )
            else:
                decision = execute_with_retry(
                    rpc_manager,
                    check_depeg_and_execute_strategy,
                    vault_address,
                    hot_wallet,
                    private_key=private_key,
                    cold_wallet=cold_wallet
                )
                
            logging.info(f"Vérification terminée. Décision : {decision}")
            
            # Mise à jour des métriques de succès
            HEALTH_DATA["last_check_status"] = "success"
            HEALTH_DATA["last_check_timestamp"] = time.time()
            HEALTH_DATA["errors_since_success"] = 0
            HEALTH_DATA["last_decision"] = decision
            HEALTH_DATA["status"] = "running"
            
            # Alerte en cas d'action majeure (retrait)
            if decision == "WITHDRAWN":
                send_alert(
                    title="🚨 Retrait de sécurité exécuté !",
                    message="Un retrait automatique des fonds a été déclenché suite à la détection d'un depeg stablecoin conforme à la stratégie.",
                    status="warning",
                    details={
                        "Vault": vault_address,
                        "Décision": decision,
                        "Bénéficiaire (Cold Wallet)": cold_wallet or hot_wallet
                    }
                )
                
            # Pause avant la prochaine vérification standard
            logging.info(f"Attente de {check_interval} secondes...")
            time.sleep(check_interval)
            
        except Exception as e:
            # Gestion d'erreurs (Panne totale RPC, etc.) -> Activation du Circuit Breaker
            HEALTH_DATA["errors_since_success"] += 1
            HEALTH_DATA["last_check_status"] = "error"
            HEALTH_DATA["last_check_timestamp"] = time.time()
            HEALTH_DATA["status"] = "circuit_breaker_active"
            
            err_msg = f"Erreur critique lors de la boucle principale du bot : {e}"
            logging.error(err_msg, exc_info=True)
            
            # Envoyer une alerte de panne
            send_alert(
                title="🚨 Alerte Panne / Erreur Bot DeFi",
                message=f"Le bot a rencontré une exception. Le circuit breaker s'active (mise en pause de sécurité).",
                status="critical",
                details={
                    "Erreur": str(e),
                    "Erreurs consécutives": HEALTH_DATA["errors_since_success"],
                    "Pause de sécurité": f"{circuit_breaker_sleep}s"
                }
            )
            
            # Pause de sécurité étendue (Circuit Breaker)
            logging.warning(f"[Circuit Breaker] Activation de la pause étendue de {circuit_breaker_sleep} secondes...")
            time.sleep(circuit_breaker_sleep)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Arrêt manuel détecté. Au revoir !")
        sys.exit(0)
