# Bot DeFi Stablecoin - Outils de Rendement

Ce dépôt contient les scripts pour mettre en place votre propre bot de trading et de placement automatisé sur les vaults de stablecoins de **Beefy Finance** sur les réseaux de couche 2 (Layer 2) avec des mesures avancées de gestion des frais, de robustesse technique, de protection contre le depeg et de sécurité OpSec.

---

## Fonctionnalités

1. **`defi_vault_finder.py` (Recherche & Analyse de Rentabilité)** : 
   - Recherche, filtre et classe les vaults actifs de stablecoins par taux de rendement (APY) sur les réseaux L2 (Arbitrum, Base, Optimism, Polygon).
   - **Calculateur de Break-Even (Amortissement)** : Permet de comparer un vault actuel avec les nouvelles opportunités en simulant l'impact des frais de Zap (entrée/sortie), de retrait et du slippage de conversion pour déterminer la rentabilité nette réelle sur une période d'amortissement ciblée.
2. **`defi_vault_trader.py` (Exécution & Protection)** :
   - **Robustesse & Failover RPC** : Gère une liste de serveurs RPC de secours. Si le nœud principal sature ou renvoie une erreur, le bot bascule automatiquement sur un RPC de secours.
   - **Retry Volatilité** : Attrape l'erreur transactionnelle `NotCalm()` de Beefy (volatilité de pool) et applique des retentatives avec un délai exponentiel (exponential backoff).
   - **Marge de Gaz** : Applique un buffer de sécurité de 15% sur les limites de gaz calculées.
   - **Protection Depeg** : Oracle hybride (Chainlink on-chain + API DefiLlama) appliquant un arbre de décision à 3 niveaux (Hold / Simulation de slippage par `previewRedeem()` / Retrait d'urgence global) lors des déviations de prix.
   - **Sécurité OpSec (Hot / Cold Wallet)** : Permet de dissocier le portefeuille d'exécution (Hot Wallet effectuant les appels et payant le gaz) et le portefeuille de stockage (Cold Wallet type Ledger ou Safe Multisig détenant les `mooTokens` de reçu).
3. **Tests unitaires** : Suite de tests complète sous `pytest` pour valider l'intégrité de la logique décisionnelle de depeg, des retentatives, du failover RPC, du flux Hot/Cold Wallet et du calculateur de break-even.

---

## Installation et Configuration

### 1. Prérequis
Assurez-vous que Python 3.10+ est installé sur votre système.

### 2. Initialisation de l'environnement virtuel
À la racine du projet, créez l'environnement virtuel et installez les dépendances :
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration des variables d'environnement
Copiez le fichier d'exemple et configurez-le :
```bash
cp .env.example .env
```
Éditez ensuite le fichier `.env` pour y ajouter vos adresses et clés privées :
```env
# URLs RPC pour le failover (séparées par des virgules)
L2_RPC_URLS=https://arb1.arbitrum.io/rpc,https://arbitrum.llamarpc.com
L2_RPC_URL=https://arb1.arbitrum.io/rpc

# --- OpSec : Ségrégation de Portefeuilles ---
HOT_WALLET_ADDRESS=0xVotreHotWalletDeBot
HOT_WALLET_PRIVATE_KEY=VotreClePriveeHotWallet

# Cold Wallet (Ledger ou Safe contenant vos mooTokens)
COLD_WALLET_ADDRESS=0xVotreColdWallet

# Adresse du vault Beefy Finance cible
DEFI_VAULT_ADDRESS=0xAdresseDuVaultBeefy
```
*Note : Si aucune clé privée n'est fournie, le trader s'exécute automatiquement en **mode simulation** (dry-run) pour sécuriser vos tests.*

---

## Utilisation

### 1. Trouver les meilleures opportunités et calculer le Break-Even

Pour lister le top des vaults stablecoins :
```bash
.venv/bin/python3 defi_vault_finder.py --limit 10
```

Pour comparer la rentabilité de migration depuis votre position actuelle en déduisant les frais et le slippage (ex : APY actuel de 5% sur un capital de 10 000 USD amorti sur 30 jours) :
```bash
.venv/bin/python3 defi_vault_finder.py --current-apy 5.0 --capital 10000 --amortization-days 30
```
*Vous pouvez également spécifier `--current-vault <id>` (ex : `silo-usdc`) pour laisser le bot interroger dynamiquement l'API de Beefy et récupérer son APY actuel.*

Options disponibles :
* `--chains` : Spécifier une ou plusieurs chaînes (ex: `--chains base arbitrum`).
* `--min-apy` : Filtrer par rendement minimum (ex: `--min-apy 3.0` pour 3%).
* `--capital` : Capital sous gestion (défaut : `10000.0` USD).
* `--amortization-days` : Période cible de break-even (défaut : `30` jours).
* `--zap-in-fee` / `--zap-out-fee` / `--withdrawal-fee` / `--slippage` : Configuration fine des frais en %.

### 2. Exécuter le Trader & Protection anti-depeg

Pour lancer une vérification de peg et appliquer la stratégie de protection automatique (Hold / Simulation / Retrait d'urgence) :
```bash
.venv/bin/python3 defi_vault_trader.py --check-peg
```

Pour **simuler** localement un krach ou depeg sur un stablecoin et observer la réaction algorithmique du bot et son calcul de slippage réel :
```bash
.venv/bin/python3 defi_vault_trader.py --simulate-depeg 0.96
```

Pour exécuter un dépôt ou retrait :
```bash
# Simuler ou exécuter un dépôt de 1000 USDC
.venv/bin/python3 defi_vault_trader.py --deposit 1000

# Simuler ou exécuter un retrait de 50 parts (mooTokens)
.venv/bin/python3 defi_vault_trader.py --withdraw 50
```

### 3. Exécuter les tests unitaires
Pour valider l'intégrité de tous les modules :
```bash
.venv/bin/pytest tests/
```

