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
import functools
from dotenv import load_dotenv
from flask import Flask, jsonify, request, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Charger les variables d'environnement
load_dotenv()

# Importer les modules du bot
from defi_vault_trader import (
    RPCManager,
    check_depeg_and_execute_strategy,
    execute_with_retry,
    OpSecMaskingFormatter
)
import defi_vault_trader
import defi_vault_finder
from alerts import send_alert

# État global partagé pour le health check et événement de réveil
trigger_event = threading.Event()
health_lock = threading.Lock()

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
    "cold_wallet_address": None,
}

# Configuration du serveur de Health Check Flask et Dashboard statique
app = Flask("DeFiBotHealthCheck", static_folder="static")

# Rate limiting pour protéger contre le DoS
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# Désactiver les logs de requêtes Flask par défaut pour ne pas polluer les logs de prod
log_werkzeug = logging.getLogger('werkzeug')
log_werkzeug.setLevel(logging.WARNING)

# --- Authentification API Key ---
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()

def require_api_key(f):
    """
    Décorateur pour protéger les endpoints sensibles avec une API key.
    Vérifie le header Authorization: Bearer <key> ou le query param ?api_key=<key>.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not ADMIN_API_KEY:
            # Pas de clé configurée = endpoints ouverts (rétrocompatibilité)
            return f(*args, **kwargs)
        
        # Vérifier le header Authorization
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and auth_header[7:] == ADMIN_API_KEY:
            return f(*args, **kwargs)
        
        # Vérifier le query param
        if request.args.get("api_key") == ADMIN_API_KEY:
            return f(*args, **kwargs)
        
        return jsonify({"error": "Unauthorized. Provide a valid API key via Authorization header or api_key query param."}), 401
    return decorated_function

@app.route("/")
def index():
    """
    Sert le tableau de bord frontend statique.
    """
    return app.send_static_file("index.html")

@app.route("/health", methods=["GET"])
@limiter.limit("60/minute")
def health_check():
    """
    Expose l'état actuel et les métriques d'exécution du bot.
    Renvoie 200 OK si le bot tourne normalement, 500 Internal Server Error si bloqué en erreur.
    """
    with health_lock:
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
            "hot_wallet_address": HEALTH_DATA["hot_wallet_address"],
            "cold_wallet_address": HEALTH_DATA["cold_wallet_address"]
        }
    
    return jsonify(response_data), status_code

@app.route("/api/status", methods=["GET"])
@limiter.limit("30/minute")
@require_api_key
def api_status():
    """
    Renvoie un état étendu pour le dashboard (toujours 200 OK pour ne pas casser le UI).
    """
    with health_lock:
        data = {
            "status": HEALTH_DATA["status"],
            "rpc_connected": HEALTH_DATA["rpc_connected"],
            "last_block_analyzed": HEALTH_DATA["last_block_analyzed"],
            "last_check_timestamp": HEALTH_DATA["last_check_timestamp"],
            "last_check_time_utc": time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(HEALTH_DATA["last_check_timestamp"])) if HEALTH_DATA["last_check_timestamp"] else None,
            "errors_since_success": HEALTH_DATA["errors_since_success"],
            "last_decision": HEALTH_DATA["last_decision"],
            "simulation_mode": HEALTH_DATA["simulation_mode"],
            "vault_address": HEALTH_DATA["vault_address"],
            "hot_wallet_address": HEALTH_DATA["hot_wallet_address"],
            "cold_wallet_address": HEALTH_DATA["cold_wallet_address"],
            "simulated_depeg_active": len(defi_vault_trader.MOCK_PRICES) > 0,
            "simulated_depeg_price": list(defi_vault_trader.MOCK_PRICES.values())[0] if defi_vault_trader.MOCK_PRICES else None
        }
    return jsonify(data)

@app.route("/api/logs", methods=["GET"])
@limiter.limit("10/minute")
@require_api_key
def api_logs():
    """
    Renvoie les dernières lignes du log de production du bot.
    """
    limit = request.args.get("limit", default=150, type=int)
    log_file_path = "logs/bot.log"
    if not os.path.exists(log_file_path):
        return jsonify([])
    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            last_lines = lines[-limit:]
            return jsonify([line.strip() for line in last_lines])
    except Exception as e:
        return jsonify([f"Error reading logs: {e}"]), 500

@app.route("/api/trigger-check", methods=["POST"])
@limiter.limit("5/minute")
@require_api_key
def api_trigger_check():
    """
    Déclenche immédiatement un cycle de vérification de peg.
    """
    trigger_event.set()
    return jsonify({"status": "triggered"})

@app.route("/api/simulate-depeg", methods=["POST"])
@limiter.limit("5/minute")
@require_api_key
def api_simulate_depeg():
    """
    Active ou désactive la simulation de depeg en injectant des prix mockés.
    Bloqué en mode production (SIMULATION_MODE=false) pour protéger les fonds.
    """
    # Sécurité : blocage en mode production
    with health_lock:
        simulation_mode = HEALTH_DATA["simulation_mode"]
    
    if not simulation_mode:
        return jsonify({"error": "Simulation depeg is disabled in production mode. Set SIMULATION_MODE=true to enable."}), 403
    
    data = request.get_json() or {}
    enabled = data.get("enabled", False)
    price = data.get("price")
    
    if enabled:
        if price is None:
            return jsonify({"error": "Price is required when simulation is enabled"}), 400
        try:
            price_float = float(price)
        except ValueError:
            return jsonify({"error": "Price must be a valid number"}), 400
            
        defi_vault_trader.MOCK_PRICES["USDC"] = price_float
        defi_vault_trader.MOCK_PRICES["USDT"] = price_float
        defi_vault_trader.MOCK_PRICES["DAI"] = price_float
        logging.info(f"[SIMULATION] Prix de simulation depeg activé à {price_float}$ pour USDC/USDT/DAI.")
    else:
        defi_vault_trader.MOCK_PRICES.clear()
        logging.info("[SIMULATION] Simulation depeg désactivée. Utilisation des oracles réels.")
        
    return jsonify({
        "simulated_depeg_active": len(defi_vault_trader.MOCK_PRICES) > 0,
        "simulated_depeg_price": price
    })

@app.route("/api/vaults", methods=["GET"])
@limiter.limit("10/minute")
def api_vaults():
    """
    Recherche, filtre et classe les vaults Beefy Finance via defi_vault_finder.
    """
    chains = request.args.getlist("chains")
    min_apy = request.args.get("min_apy", default=0.0, type=float)
    try:
        vaults, apys = defi_vault_finder.fetch_beefy_data()
        chains_filter = set(chains) if chains else None
        # Convertir min_apy en décimal (ex: 5.0% -> 0.05)
        min_apy_decimal = min_apy / 100.0 if min_apy else 0.0
        filtered = defi_vault_finder.filter_and_rank_vaults(vaults, apys, chains=chains_filter, min_apy=min_apy_decimal)
        return jsonify(filtered)
    except Exception as e:
        logging.error(f"Error serving vaults list: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/calculator", methods=["POST"])
@limiter.limit("30/minute")
def api_calculator():
    """
    Exécute le calculateur de break-even.
    """
    data = request.get_json() or {}
    try:
        current_apy = float(data.get("current_apy", 0.0)) / 100.0
        new_apy = float(data.get("new_apy", 0.0)) / 100.0
        capital = float(data.get("capital", 10000.0))
        days = int(data.get("amortization_days", 30))
        zap_in = float(data.get("zap_in_fee", 0.05))
        zap_out = float(data.get("zap_out_fee", 0.05))
        withdraw = float(data.get("withdrawal_fee", 0.05))
        slippage = float(data.get("slippage", 0.1))
        
        is_profitable, net_profit, break_even_apy, total_friction = defi_vault_finder.calculate_break_even(
            current_apy, new_apy, capital, days, zap_in, zap_out, withdraw, slippage
        )
        
        return jsonify({
            "is_profitable": is_profitable,
            "net_profit": net_profit,
            "break_even_apy": break_even_apy * 100.0 if break_even_apy != float('inf') else None,
            "total_friction": total_friction
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

def run_health_server():
    """
    Exécute le serveur de health check / dashboard sur le port configuré.
    """
    port = int(os.getenv("PORT", 8080))
    logging.info(f"[HTTP] Lancement du serveur health check / dashboard sur le port {port}...")
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        logging.error(f"[HTTP] Impossible de démarrer le serveur : {e}")

def setup_logging():
    """
    Configure les logs de production (StreamHandler et RotatingFileHandler).
    """
    os.makedirs("logs", exist_ok=True)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Format standardisé des logs de production avec masquage OpSec
    formatter = OpSecMaskingFormatter(
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
    
    with health_lock:
        HEALTH_DATA["simulation_mode"] = simulation_mode
        HEALTH_DATA["vault_address"] = vault_address
        HEALTH_DATA["hot_wallet_address"] = hot_wallet
        HEALTH_DATA["cold_wallet_address"] = cold_wallet
    
    logging.info("=== INITIALISATION DU BOT DEFI ===")
    logging.info(f"Mode Simulation : {simulation_mode}")
    logging.info(f"Vault de destination : {vault_address}")
    logging.info(f"Hot Wallet (Bot) : {hot_wallet}")
    logging.info(f"Cold Wallet (Safe/Ledger) : {cold_wallet or 'Non configuré (Retraits directs activés)'}")
    
    if ADMIN_API_KEY:
        logging.info("[SECURITE] Authentification API key activée sur les endpoints sensibles.")
    else:
        logging.warning("[SECURITE] ADMIN_API_KEY non configurée. Les endpoints sensibles sont ouverts.")
    
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
    
    with health_lock:
        HEALTH_DATA["status"] = "running"
    
    # 4. Boucle infinie d'exécution
    while True:
        try:
            # Réinitialiser l'événement au début de l'itération
            trigger_event.clear()
            
            logging.info("Exécution de la vérification périodique...")
            w3 = rpc_manager.get_w3()
            
            if w3:
                with health_lock:
                    HEALTH_DATA["rpc_connected"] = True
                try:
                    block_number = w3.eth.block_number
                    with health_lock:
                        HEALTH_DATA["last_block_analyzed"] = block_number
                except Exception as block_err:
                    logging.warning(f"Impossible d'interroger le numéro du dernier bloc : {block_err}")
            else:
                with health_lock:
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
            with health_lock:
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
                
            # Pause avant la prochaine vérification standard ou réveil événementiel
            logging.info(f"Attente de {check_interval} secondes (ou réveil immédiat)...")
            trigger_event.wait(timeout=check_interval)
            
        except Exception as e:
            # Gestion d'erreurs (Panne totale RPC, etc.) -> Activation du Circuit Breaker
            with health_lock:
                HEALTH_DATA["errors_since_success"] += 1
                HEALTH_DATA["last_check_status"] = "error"
                HEALTH_DATA["last_check_timestamp"] = time.time()
                HEALTH_DATA["status"] = "circuit_breaker_active"
            
            err_msg = f"Erreur critique lors de la boucle principale du bot : {e}"
            logging.error(err_msg, exc_info=True)
            
            # Envoyer une alerte de panne
            with health_lock:
                errors_count = HEALTH_DATA["errors_since_success"]
            
            send_alert(
                title="🚨 Alerte Panne / Erreur Bot DeFi",
                message=f"Le bot a rencontré une exception. Le circuit breaker s'active (mise en pause de sécurité).",
                status="critical",
                details={
                    "Erreur": str(e),
                    "Erreurs consécutives": errors_count,
                    "Pause de sécurité": f"{circuit_breaker_sleep}s"
                }
            )
            
            # Pause de sécurité étendue (Circuit Breaker) avec réveil événementiel possible
            logging.warning(f"[Circuit Breaker] Activation de la pause étendue de {circuit_breaker_sleep} secondes...")
            trigger_event.wait(timeout=circuit_breaker_sleep)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Arrêt manuel détecté. Au revoir !")
        sys.exit(0)
