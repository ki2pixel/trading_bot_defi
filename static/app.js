// CONFIGURATION
const POLL_INTERVAL_MS = 3000;
let lastLogLinesCount = 0;
let isSimulationActive = false;

// DOM ELEMENTS
const elBotStatusText = document.getElementById('bot-status-text');
const elBotStatusIndicator = document.getElementById('bot-status-indicator');
const elSimModeIndicator = document.getElementById('simulation-mode-indicator');
const elRpcStatus = document.getElementById('rpc-status');
const elLastBlock = document.getElementById('last-block');
const elLastCheck = document.getElementById('last-check');
const elLastDecision = document.getElementById('last-decision');
const elHotWalletAddr = document.getElementById('hot-wallet-addr');
const elColdWalletAddr = document.getElementById('cold-wallet-addr');
const elVaultAddr = document.getElementById('vault-addr');

const elBtnTriggerCheck = document.getElementById('btn-trigger-check');
const elSimPriceRange = document.getElementById('sim-price-range');
const elSimPriceVal = document.getElementById('sim-price-val');
const elBtnEnableSim = document.getElementById('btn-enable-simulation');
const elBtnDisableSim = document.getElementById('btn-disable-simulation');

const elLogFilter = document.getElementById('log-filter');
const elAutoscrollToggle = document.getElementById('autoscroll-toggle');
const elBtnClearTerminal = document.getElementById('btn-clear-terminal');
const elTerminalScreen = document.getElementById('terminal-screen');

const elBtnSearchVaults = document.getElementById('btn-search-vaults');
const elVaultsLoader = document.getElementById('vaults-loader');
const elVaultsTbody = document.getElementById('vaults-tbody');
const elMinApyInput = document.getElementById('min-apy-input');

const elCalcForm = document.getElementById('calc-form');
const elCalcResultsBox = document.getElementById('calc-results-box');
const elCalcProfitStatus = document.getElementById('calc-profit-status');
const elCalcResultTitle = document.getElementById('calc-result-title');
const elCalcResultSubtitle = document.getElementById('calc-result-subtitle');
const elResNetProfit = document.getElementById('res-net-profit');
const elResBeApy = document.getElementById('res-be-apy');
const elResFriction = document.getElementById('res-friction');

const elToast = document.getElementById('toast');
const elToastIcon = document.getElementById('toast-icon');
const elToastMessage = document.getElementById('toast-message');

// TOAST SYSTEM
function showToast(message, type = 'info') {
    elToastMessage.textContent = message;
    
    // Reset classes
    elToast.className = 'toast';
    elToast.classList.add(`toast-${type}`);
    
    // Set icon
    let iconName = 'info';
    if (type === 'success') iconName = 'check-circle-2';
    if (type === 'error') iconName = 'x-circle';
    
    elToastIcon.setAttribute('data-lucide', iconName);
    lucide.createIcons();
    
    // Show toast
    elToast.classList.remove('hidden');
    
    // Hide toast after 3.5s
    setTimeout(() => {
        elToast.classList.add('hidden');
    }, 3500);
}

// COPY TO CLIPBOARD
window.copyText = function(elementId) {
    const text = document.getElementById(elementId).textContent;
    navigator.clipboard.writeText(text).then(() => {
        showToast('Adresse copiée dans le presse-papiers !', 'success');
    }).catch(err => {
        showToast('Échec de la copie', 'error');
        console.error('Error copying text: ', err);
    });
};

// FORMAT DATE
function formatTimestamp(epochSec) {
    if (!epochSec) return 'N/A';
    const date = new Date(epochSec * 1000);
    return date.toLocaleTimeString('fr-FR') + ' ' + date.toLocaleDateString('fr-FR');
}

// 1. STATE & INFO POLLING
async function pollStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error('API status failure');
        
        const data = await response.json();
        
        // Update Bot status badge
        elBotStatusIndicator.className = 'status-badge';
        if (data.status === 'running') {
            elBotStatusIndicator.classList.add('status-running');
            elBotStatusText.textContent = 'En cours';
        } else if (data.status === 'circuit_breaker_active') {
            elBotStatusIndicator.classList.add('status-circuit-breaker');
            elBotStatusText.textContent = 'Circuit Breaker Actif';
        } else {
            elBotStatusIndicator.classList.add('status-starting');
            elBotStatusText.textContent = 'Démarrage';
        }
        
        // Update Simulation badge
        if (data.simulation_mode) {
            elSimModeIndicator.classList.remove('hidden');
        } else {
            elSimModeIndicator.classList.add('hidden');
        }
        
        // RPC status
        if (data.rpc_connected) {
            elRpcStatus.className = 'value text-success';
            elRpcStatus.innerHTML = '<i data-lucide="wifi"></i> Connecté';
        } else {
            elRpcStatus.className = 'value text-danger';
            elRpcStatus.innerHTML = '<i data-lucide="wifi-off"></i> Déconnecté';
        }
        
        // Last block, check, decision
        elLastBlock.textContent = data.last_block_analyzed || 'N/A';
        elLastCheck.textContent = formatTimestamp(data.last_check_timestamp);
        
        // Decision highlight color
        elLastDecision.textContent = data.last_decision || 'HOLD';
        elLastDecision.className = 'value highlight-box';
        if (data.last_decision === 'WITHDRAWN') {
            elLastDecision.classList.add('text-danger');
        } else if (data.last_decision === 'HOLD') {
            elLastDecision.classList.add('text-success');
        }
        
        // Wallet addresses
        elHotWalletAddr.textContent = data.hot_wallet_address || 'N/A';
        elColdWalletAddr.textContent = data.cold_wallet_address || 'Non configuré';
        elVaultAddr.textContent = data.vault_address || 'N/A';
        
        // Update simulator simulation states
        isSimulationActive = data.simulated_depeg_active;
        if (isSimulationActive) {
            elBtnEnableSim.disabled = true;
            elBtnDisableSim.disabled = false;
            // Update range input visual if synced
            if (data.simulated_depeg_price) {
                elSimPriceRange.value = data.simulated_depeg_price;
                elSimPriceVal.textContent = parseFloat(data.simulated_depeg_price).toFixed(2) + '$';
            }
        } else {
            elBtnEnableSim.disabled = false;
            elBtnDisableSim.disabled = true;
        }
        
        lucide.createIcons();
    } catch (error) {
        console.error('Error polling bot status:', error);
    }
}

// 2. TERMINAL LOGS POLLING & FILTERING
function getLogLevelClass(line) {
    if (line.includes('[CRITICAL]')) return 'log-critical';
    if (line.includes('[ERROR]')) return 'log-error';
    if (line.includes('[WARNING]')) return 'log-warning';
    if (line.includes('[INFO]')) return 'log-info';
    return '';
}

function shouldShowLogLine(line, filter) {
    if (filter === 'ALL') return true;
    if (filter === 'INFO') {
        return line.includes('[INFO]') || line.includes('[WARNING]') || line.includes('[ERROR]') || line.includes('[CRITICAL]');
    }
    if (filter === 'WARNING') {
        return line.includes('[WARNING]') || line.includes('[ERROR]') || line.includes('[CRITICAL]');
    }
    if (filter === 'ERROR') {
        return line.includes('[ERROR]') || line.includes('[CRITICAL]');
    }
    return true;
}

async function pollLogs() {
    try {
        const response = await fetch('/api/logs?limit=150');
        if (!response.ok) throw new Error('API logs failure');
        
        const lines = await response.json();
        
        // Only re-render if we received new lines or a change
        if (lines.length !== lastLogLinesCount) {
            lastLogLinesCount = lines.length;
            renderLogs(lines);
        }
    } catch (error) {
        console.error('Error fetching logs:', error);
    }
}

function renderLogs(lines) {
    const activeFilter = elLogFilter.value;
    
    // Clear screen first (keep system startup lines if empty)
    elTerminalScreen.innerHTML = '';
    
    if (lines.length === 0) {
        elTerminalScreen.innerHTML = '<div class="log-line log-system">Le fichier de logs est vide ou inexistant.</div>';
        return;
    }
    
    let renderedCount = 0;
    lines.forEach(line => {
        if (shouldShowLogLine(line, activeFilter)) {
            const elLine = document.createElement('div');
            elLine.className = 'log-line ' + getLogLevelClass(line);
            elLine.textContent = line;
            elTerminalScreen.appendChild(elLine);
            renderedCount++;
        }
    });
    
    if (renderedCount === 0) {
        elTerminalScreen.innerHTML = '<div class="log-line log-system">Aucun log ne correspond au filtre actif.</div>';
    }
    
    // Autoscroll to bottom
    if (elAutoscrollToggle.checked) {
        elTerminalScreen.scrollTop = elTerminalScreen.scrollHeight;
    }
}

// 3. ACTION BUTTON (TRIGGER CHECK)
elBtnTriggerCheck.addEventListener('click', async () => {
    elBtnTriggerCheck.disabled = true;
    const originalContent = elBtnTriggerCheck.innerHTML;
    elBtnTriggerCheck.innerHTML = '<i class="spinner" style="width:14px;height:14px;border-width:2px;display:inline-block;margin:0;"></i> Déclenchement...';
    
    try {
        const response = await fetch('/api/trigger-check', { method: 'POST' });
        if (response.ok) {
            showToast('Vérification du peg forcée avec succès !', 'success');
            // Wait slightly and fetch status
            setTimeout(pollStatus, 500);
            setTimeout(pollLogs, 1000);
        } else {
            showToast('Erreur lors du déclenchement', 'error');
        }
    } catch (e) {
        showToast('Erreur réseau', 'error');
    } finally {
        setTimeout(() => {
            elBtnTriggerCheck.disabled = false;
            elBtnTriggerCheck.innerHTML = originalContent;
            lucide.createIcons();
        }, 1500);
    }
});

// 4. SIMULATION DEPEG RANGE & BUTTONS
elSimPriceRange.addEventListener('input', (e) => {
    elSimPriceVal.textContent = parseFloat(e.target.value).toFixed(2) + '$';
});

elBtnEnableSim.addEventListener('click', async () => {
    const price = parseFloat(elSimPriceRange.value);
    try {
        const response = await fetch('/api/simulate-depeg', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: true, price: price })
        });
        
        if (response.ok) {
            showToast(`Simulation activée : Peg simulé à ${price.toFixed(2)}$`, 'warning');
            pollStatus();
            setTimeout(pollLogs, 500);
        } else {
            showToast('Erreur lors de l\'activation de la simulation', 'error');
        }
    } catch (e) {
        showToast('Erreur réseau', 'error');
    }
});

elBtnDisableSim.addEventListener('click', async () => {
    try {
        const response = await fetch('/api/simulate-depeg', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: false })
        });
        
        if (response.ok) {
            showToast('Simulation depeg désactivée. Oracles réels rétablis.', 'success');
            pollStatus();
            setTimeout(pollLogs, 500);
        } else {
            showToast('Erreur lors de la désactivation de la simulation', 'error');
        }
    } catch (e) {
        showToast('Erreur réseau', 'error');
    }
});

// CLEAR TERMINAL SCREEN ONLY
elBtnClearTerminal.addEventListener('click', () => {
    elTerminalScreen.innerHTML = '<div class="log-line log-system">Console effacée par l\'utilisateur. En attente de nouveaux logs...</div>';
    lastLogLinesCount = 0;
});

// RE-RENDER LOGS ON FILTER CHANGE
elLogFilter.addEventListener('change', () => {
    // Re-fetch log data and trigger render
    pollLogs();
});

// 5. VAULT FINDER (BEEFY FINANCE API)
elBtnSearchVaults.addEventListener('click', async () => {
    elVaultsLoader.classList.remove('hidden');
    elVaultsTbody.innerHTML = '';
    
    // Construct chains query string
    const chainsChecked = Array.from(document.querySelectorAll('input[name="chains"]:checked')).map(cb => cb.value);
    const minApy = parseFloat(elMinApyInput.value) || 0;
    
    let queryParams = [];
    chainsChecked.forEach(c => queryParams.push(`chains=${c}`));
    queryParams.push(`min_apy=${minApy}`);
    
    const url = `/api/vaults?${queryParams.join('&')}`;
    
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error('API vaults fetch error');
        
        const vaults = await response.json();
        
        if (vaults.length === 0) {
            elVaultsTbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-muted">Aucun vault stablecoin actif trouvé avec ces filtres</td>
                </tr>
            `;
            return;
        }
        
        vaults.forEach(vault => {
            const tr = document.createElement('tr');
            
            // Format chain badge
            const chainClass = `badge-${vault.chain}`;
            const chainBadge = `<span class="badge-chain ${chainClass}">${vault.chain}</span>`;
            
            // APY percentage formatting
            const apyPct = (vault.apy * 100).toFixed(2);
            
            // Shortened address for display, but keep full address in DOM for copying
            const shortAddr = vault.vaultAddress ? `${vault.vaultAddress.substring(0, 6)}...${vault.vaultAddress.substring(vault.vaultAddress.length - 4)}` : 'N/A';
            const addressHtml = vault.vaultAddress ? `
                <div style="display:inline-flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:0.75rem;">
                    <span title="${vault.vaultAddress}">${shortAddr}</span>
                    <span id="addr-${vault.id}" class="hidden">${vault.vaultAddress}</span>
                    <button class="btn-copy" onclick="copyText('addr-${vault.id}')" title="Copier l'adresse du vault"><i data-lucide="copy" style="width:12px;height:12px;"></i></button>
                </div>
            ` : 'N/A';
            
            // Assets string list
            const assetsList = vault.assets.join(', ');
            
            tr.innerHTML = `
                <td>${chainBadge}</td>
                <td><strong>${escapeHtml(vault.name)}</strong></td>
                <td><span class="highlight-box">${escapeHtml(vault.platform)}</span></td>
                <td class="text-success" style="font-weight:600;">${apyPct}%</td>
                <td>${addressHtml}</td>
                <td>${escapeHtml(assetsList)}</td>
                <td>
                    <button class="btn btn-secondary btn-icon-text" onclick="loadToCalculator(${vault.apy * 100})">
                        <i data-lucide="calculator"></i> Calculer
                    </button>
                </td>
            `;
            elVaultsTbody.appendChild(tr);
        });
        
        lucide.createIcons();
        showToast(`${vaults.length} vaults chargés.`, 'success');
        
    } catch (error) {
        showToast('Impossible de récupérer les vaults Beefy API.', 'error');
        elVaultsTbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center text-danger">Erreur : ${error.message}</td>
            </tr>
        `;
    } finally {
        elVaultsLoader.classList.add('hidden');
    }
});

// HTML ESCAPE HELPER
function escapeHtml(unsafe) {
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

// LOAD VAULT APY INTO CALCULATOR
window.loadToCalculator = function(apyPercent) {
    document.getElementById('calc-new-apy').value = apyPercent.toFixed(2);
    showToast(`Nouvelle cible configurée à ${apyPercent.toFixed(2)}% APY`, 'info');
    
    // Smooth scroll to calculator
    elCalcForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
};

// 6. MIGRATION CALCULATOR
elCalcForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const capital = parseFloat(document.getElementById('calc-capital').value);
    const days = parseInt(document.getElementById('calc-days').value);
    const currentApy = parseFloat(document.getElementById('calc-current-apy').value);
    const newApy = parseFloat(document.getElementById('calc-new-apy').value);
    
    const zapInFee = parseFloat(document.getElementById('calc-zap-in').value);
    const zapOutFee = parseFloat(document.getElementById('calc-zap-out').value);
    const withdrawalFee = parseFloat(document.getElementById('calc-withdraw').value);
    const slippage = parseFloat(document.getElementById('calc-slippage').value);
    
    try {
        const response = await fetch('/api/calculator', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                capital: capital,
                amortization_days: days,
                current_apy: currentApy,
                new_apy: newApy,
                zap_in_fee: zapInFee,
                zap_out_fee: zapOutFee,
                withdrawal_fee: withdrawalFee,
                slippage: slippage
            })
        });
        
        if (!response.ok) throw new Error('API calculator error');
        
        const res = await response.json();
        
        // Show results
        elCalcResultsBox.classList.remove('hidden');
        
        // Update values
        const sign = res.net_profit >= 0 ? '+' : '';
        elResNetProfit.textContent = `${sign}${res.net_profit.toFixed(2)} $`;
        
        if (res.net_profit >= 0) {
            elResNetProfit.className = 'value text-success';
            elCalcProfitStatus.className = 'profit-banner profit-banner-profitable';
            elCalcResultTitle.textContent = 'Migration Rentable !';
            elCalcResultSubtitle.textContent = `Gain net estimé de ${res.net_profit.toFixed(2)}$ après déduction des frais de friction.`;
            elCalcProfitStatus.querySelector('.profit-icon').innerHTML = '<i data-lucide="check-circle-2"></i>';
        } else {
            elResNetProfit.className = 'value text-danger';
            elCalcProfitStatus.className = 'profit-banner profit-banner-unprofitable';
            elCalcResultTitle.textContent = 'Non Rentable';
            elCalcResultSubtitle.textContent = `Perte nette de ${Math.abs(res.net_profit).toFixed(2)}$ sur cette période de ${days} jours.`;
            elCalcProfitStatus.querySelector('.profit-icon').innerHTML = '<i data-lucide="alert-triangle"></i>';
        }
        
        elResBeApy.textContent = res.break_even_apy ? `${res.break_even_apy.toFixed(2)}%` : 'Infinity';
        elResFriction.textContent = `${res.total_friction.toFixed(3)}%`;
        
        lucide.createIcons();
        
    } catch (error) {
        showToast('Erreur dans le calcul : ' + error.message, 'error');
    }
});

// INITIALIZATION
pollStatus();
pollLogs();

// Start intervals
setInterval(pollStatus, POLL_INTERVAL_MS);
setInterval(pollLogs, POLL_INTERVAL_MS);
