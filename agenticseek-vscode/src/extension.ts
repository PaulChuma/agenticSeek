// The module 'vscode' contains the VS Code extensibility API
// Import the module and reference it with the alias vscode in your code below
import * as vscode from 'vscode';

export function activate(context: vscode.ExtensionContext) {
	console.log('AgenticSeek VS Code extension is now active!');

	// Register command to open AgenticSeek panel
	const openPanelCmd = vscode.commands.registerCommand('agenticseek.openPanel', () => {
		AgenticSeekPanel.createOrShow(context.extensionUri);
	});
	context.subscriptions.push(openPanelCmd);



class AgenticSeekPanel {
	public static currentPanel: AgenticSeekPanel | undefined;
	private readonly _panel: vscode.WebviewPanel;
	private readonly _extensionUri: vscode.Uri;
	private _disposables: vscode.Disposable[] = [];

	public static createOrShow(extensionUri: vscode.Uri) {
		const column = vscode.window.activeTextEditor ? vscode.window.activeTextEditor.viewColumn : undefined;
		if (AgenticSeekPanel.currentPanel) {
			AgenticSeekPanel.currentPanel._panel.reveal(column);
			return;
		}
		const panel = vscode.window.createWebviewPanel(
			'agenticseek',
			'AgenticSeek',
			column || vscode.ViewColumn.One,
			{
				enableScripts: true,
				retainContextWhenHidden: true
			}
		);
		AgenticSeekPanel.currentPanel = new AgenticSeekPanel(panel, extensionUri);
	}

	private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
		this._panel = panel;
		this._extensionUri = extensionUri;
		this._panel.webview.html = this._getHtmlForWebview();

		// Handle messages from the webview
		this._panel.webview.onDidReceiveMessage(
			async (message) => {
				const backendUrl = vscode.workspace.getConfiguration('agenticseek').get<string>('backendUrl') || 'http://localhost:8000';
				switch (message.command) {
					case 'buildProject': {
						const res = await callAgenticSeekApi(`${backendUrl}/build_project`, {
							query: message.description
						});
						this._panel.webview.postMessage({ type: 'buildProjectResult', result: res });
						break;
					}
					case 'runCommand': {
						const res = await callAgenticSeekApi(`${backendUrl}/run_project_command`, {
							project_name: message.projectName || 'ai_engineer_assistant',
							command: message.command
						});
						this._panel.webview.postMessage({ type: 'runCommandResult', result: res });
						break;
					}
					case 'getStatus': {
						const res = await callAgenticSeekApi(`${backendUrl}/project_status`);
						this._panel.webview.postMessage({ type: 'statusResult', result: res });
						break;
					}
				}
			},
			undefined,
			this._disposables
		);

		this._panel.onDidDispose(() => this.dispose(), null, this._disposables);
	}

	public dispose() {
		AgenticSeekPanel.currentPanel = undefined;
		this._panel.dispose();
		while (this._disposables.length) {
			const x = this._disposables.pop();
			if (x) {
				x.dispose();
			}
		}
	}

	private _getHtmlForWebview(): string {
		// Interactive terminal UI for demonstration
		return [
			'<!DOCTYPE html>',
			'<html lang="en">',
			'<head>',
			'  <meta charset="UTF-8">',
			'  <meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\' \'unsafe-eval\';">',
			'  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
			'  <title>AgenticSeek</title>',
			'  <style>',
			'    #terminal-output { background: #181818; color: #e0e0e0; padding: 8px; min-height: 200px; font-family: monospace; white-space: pre-wrap; border-radius: 4px; }',
			'    #terminal-input { width: 100%; font-family: monospace; margin-top: 8px; }',
			'    #terminal-bar { display: flex; gap: 8px; margin-top: 8px; }',
			'  </style>',
			'</head>',
			'<body>',
			'  <h2>AgenticSeek VS Code Integration</h2>',
			'  <textarea id="description" rows="3" style="width:100%" placeholder="Project description..."></textarea><br>',
			'  <button onclick="buildProject()">Build Project</button>',
			'  <button onclick="getStatus()">Get Status</button>',
			'  <button id="commandPaletteBtn" title="Открыть палитру команд VS Code (Ctrl+Shift+P)">Ctrl+Shift+P</button>',
			'  <div style="margin-top:16px;">',
			'    <div id="terminal-output"></div>',
			'    <div id="terminal-bar">',
			'      <input id="terminal-input" type="text" placeholder="Type shell or Docker command and press Enter..." />',
			'      <button onclick="sendTerminalCommand()">Send</button>',
			'    </div>',
			'  </div>',
			'  <script>',
			'    document.getElementById("commandPaletteBtn").onclick = function() {',
			'      alert("Откройте палитру команд VS Code сочетанием Ctrl+Shift+P и выберите 'AgenticSeek: Open Panel' для быстрого доступа к функциям расширения.");',
			'      document.getElementById("terminal-input").focus();',
			'    };',
			'    const vscode = acquireVsCodeApi();',
			'    let currentProject = "ai_engineer_assistant";',
			'    function buildProject() {',
			'      const description = document.getElementById("description").value;',
			'      vscode.postMessage({ command: "buildProject", description });',
			'    }',
			'    function getStatus() {',
			'      vscode.postMessage({ command: "getStatus" });',
			'    }',
			'    function sendTerminalCommand() {',
			'      const cmd = document.getElementById("terminal-input").value;',
			'      if (!cmd) return;',
			'      appendTerminalOutput("$ " + cmd + "\n");',
			'      vscode.postMessage({ command: "runCommand", command: cmd, projectName: currentProject });',
			'      document.getElementById("terminal-input").value = "";',
			'    }',
			'    function appendTerminalOutput(text) {',
			'      const out = document.getElementById("terminal-output");',
			'      out.textContent += text;',
			'      out.scrollTop = out.scrollHeight;',
			'    }',
			'    document.getElementById("terminal-input").addEventListener("keydown", function(e) {',
			'      if (e.key === "Enter") {',
			'        e.preventDefault();',
			'        sendTerminalCommand();',
			'      }',
			'    });',
			'    window.addEventListener("message", event => {',
			'      const msg = event.data;',
			'      if (msg.type === "buildProjectResult") {',
			'        appendTerminalOutput("Project built: " + JSON.stringify(msg.result) + "\n");',
			'      } else if (msg.type === "runCommandResult") {',
			'        if (msg.result && msg.result.status === "started") {',
			'          appendTerminalOutput("Command started...\n");',
			'        } else {',
			'          appendTerminalOutput(JSON.stringify(msg.result) + "\n");',
			'        }',
			'      } else if (msg.type === "statusResult") {',
			'        if (msg.result && msg.result.output) {',
			'          appendTerminalOutput(msg.result.output + "\n");',
			'        } else {',
			'          appendTerminalOutput(JSON.stringify(msg.result) + "\n");',
			'        }',
			'      }',
			'    });',
			'  <\/script>',
			'</body>',
			'</html>'
		].join('\n');
	}
}

function deactivate() {}

// --- REST API client for AgenticSeek backend ---
async function callAgenticSeekApi(url: string, body?: any) {
	try {
		const res = await fetch(url, {
			method: body ? 'POST' : 'GET',
			headers: { 'Content-Type': 'application/json' },
			body: body ? JSON.stringify(body) : undefined
		});
		return await res.json();
	} catch (e) {
		return { error: String(e) };
	}
}
}
