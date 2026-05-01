import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var loadingOverlay: NSView?
    
    // This will be replaced by the build script with the actual path
    var projectPath: String = "PROJECT_PATH_PLACEHOLDER"

    func applicationDidFinishLaunching(_ notification: Notification) {
        print("🚀 Launching Local LLM from: \(projectPath)")

        // 1. Force a clean state by stopping any old instances first
        runScript(name: "./stop.sh", synchronous: true)
        
        // 2. Clear all website data (Cache, Cookies, Local Storage)
        let websiteDataTypes = NSSet(array: [WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache, WKWebsiteDataTypeLocalStorage])
        let dateFrom = Date(timeIntervalSince1970: 0)
        WKWebsiteDataStore.default().removeData(ofTypes: websiteDataTypes as! Set<String>, modifiedSince: dateFrom) {
            print("✅ Web cache cleared")
        }
        
        // Give the OS a moment to free the port/files
        Thread.sleep(forTimeInterval: 1.0)
        
        // 3. Start the fresh instance
        runScript(name: "./start.sh", synchronous: false)

        // 4. Setup Menu Bar (required for ⌘C/⌘V/⌘X/⌘A/⌘Z shortcuts)
        setupMenu()

        // 5. Setup Window
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1400, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.center()
        window.title = "Local LLM Chat"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.delegate = self

        // 6. Setup WebView (hidden until content loads)
        let config = WKWebViewConfiguration()
        
        // Enable Developer Tools (Right-click -> Inspect Element)
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.translatesAutoresizingMaskIntoConstraints = false
        // Hide the WebView's default white background so the loading overlay shows through
        webView.setValue(false, forKey: "drawsBackground")
        window.contentView?.addSubview(webView)
        
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: window.contentView!.topAnchor),
            webView.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor),
        ])
        
        // 7. Setup loading overlay (on top of WebView)
        setupLoadingOverlay()
        
        loadWhenReady()
        
        window.makeKeyAndOrderFront(nil)
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    
    // MARK: - Menu Bar
    
    func setupMenu() {
        let mainMenu = NSMenu()
        
        // App Menu ("Local LLM" menu)
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Local LLM", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Hide Local LLM", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthersItem = NSMenuItem(title: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthersItem.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(hideOthersItem)
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Quit Local LLM", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)
        
        // Edit Menu (required for ⌘C, ⌘V, ⌘X, ⌘A, ⌘Z to work in WKWebView)
        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)
        
        // Window Menu
        let windowMenuItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        let fullScreenItem = NSMenuItem(title: "Toggle Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullScreenItem.keyEquivalentModifierMask = [.command, .control]
        windowMenu.addItem(fullScreenItem)
        windowMenuItem.submenu = windowMenu
        mainMenu.addItem(windowMenuItem)
        
        NSApp.mainMenu = mainMenu
        NSApp.windowsMenu = windowMenu
    }
    
    // MARK: - Loading Overlay
    
    func setupLoadingOverlay() {
        // Detect system appearance (light vs dark mode)
        let isDark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        
        // Full-screen overlay
        let overlay = NSView(frame: .zero)
        overlay.translatesAutoresizingMaskIntoConstraints = false
        overlay.wantsLayer = true
        overlay.layer?.backgroundColor = isDark
            ? NSColor(red: 0.07, green: 0.07, blue: 0.09, alpha: 1.0).cgColor
            : NSColor(red: 0.98, green: 0.98, blue: 0.98, alpha: 1.0).cgColor
        
        // Container for spinner + label (vertically centered together)
        let stack = NSStackView()
        stack.translatesAutoresizingMaskIntoConstraints = false
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 16
        
        // Spinner — force the opposite appearance so it contrasts with our background
        let spinner = NSProgressIndicator(frame: NSRect(x: 0, y: 0, width: 32, height: 32))
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.translatesAutoresizingMaskIntoConstraints = false
        // Dark bg → light (aqua) spinner renders dark... actually:
        // .darkAqua appearance → light/white spinner (for dark backgrounds)
        // .aqua appearance → dark/gray spinner (for light backgrounds)
        spinner.appearance = isDark
            ? NSAppearance(named: .darkAqua)
            : NSAppearance(named: .aqua)
        spinner.startAnimation(nil)
        
        // Label
        let label = NSTextField(labelWithString: "Starting Local LLM...")
        label.translatesAutoresizingMaskIntoConstraints = false
        label.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        label.textColor = isDark
            ? NSColor(white: 0.55, alpha: 1.0)
            : NSColor(white: 0.35, alpha: 1.0)
        label.alignment = .center
        
        stack.addArrangedSubview(spinner)
        stack.addArrangedSubview(label)
        overlay.addSubview(stack)
        
        window.contentView?.addSubview(overlay)
        self.loadingOverlay = overlay
        
        NSLayoutConstraint.activate([
            overlay.topAnchor.constraint(equalTo: window.contentView!.topAnchor),
            overlay.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor),
            overlay.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor),
            overlay.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor),
            stack.centerXAnchor.constraint(equalTo: overlay.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: overlay.centerYAnchor),
        ])
    }
    
    func hideLoadingOverlay() {
        guard let overlay = loadingOverlay else { return }
        // Re-enable WebView background drawing before revealing it
        webView.setValue(true, forKey: "drawsBackground")
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = 0.4
            overlay.animator().alphaValue = 0
        }, completionHandler: {
            overlay.removeFromSuperview()
            self.loadingOverlay = nil
        })
    }

    func loadWhenReady() {
        let url = URL(string: "http://127.0.0.1:8000")!
        
        func attemptLoad() {
            let task = URLSession.shared.dataTask(with: url) { [weak self] _, response, error in
                guard let self = self else { return }
                
                if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                    print("✅ Server responded with 200, loading WebView...")
                    DispatchQueue.main.async {
                        let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 30.0)
                        self.webView.load(request)
                    }
                } else {
                    let desc = error?.localizedDescription ?? "non-200 status"
                    print("⏳ Server not ready: \(desc). Retrying in 1s...")
                    DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) {
                        attemptLoad()
                    }
                }
            }
            task.resume()
        }
        attemptLoad()
    }
    
    // MARK: - WKNavigationDelegate
    
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        print("✅ WebView finished loading: \(webView.url?.absoluteString ?? "nil")")
        DispatchQueue.main.async {
            self.hideLoadingOverlay()
        }
    }
    
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        print("❌ WebView navigation failed: \(error.localizedDescription)")
        // Retry after a delay — the server may have restarted
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            self.loadWhenReady()
        }
    }
    
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        print("❌ WebView provisional navigation failed: \(error.localizedDescription)")
        // This fires for connection-refused, SSL errors, etc.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            self.loadWhenReady()
        }
    }
    
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let httpResponse = navigationResponse.response as? HTTPURLResponse {
            print("📡 WebView received HTTP \(httpResponse.statusCode) for \(httpResponse.url?.absoluteString ?? "nil")")
        }
        decisionHandler(.allow)
    }

    func windowWillClose(_ notification: Notification) {
        shutdown()
    }
    
    func applicationWillTerminate(_ notification: Notification) {
        shutdown()
    }
    
    func shutdown() {
        print("🛑 Shutting down Local LLM...")
        runScript(name: "./stop.sh", synchronous: true)
        NSApplication.shared.terminate(self)
    }

    func runScript(name: String, synchronous: Bool) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        // Use -l (login shell) to ensure ~/.zprofile and /etc/zprofile are sourced,
        // which adds /opt/homebrew/bin to PATH on Apple Silicon Macs.
        // Without this, the .app bundle inherits a minimal environment where
        // brew, python3, etc. are not found — causing start.sh to silently fail.
        process.arguments = ["-l", "-c", "cd '\(projectPath)' && \(name)"]
        
        // Create pipes to capture output
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        try? process.run()
        
        if synchronous {
            process.waitUntilExit()
            
            // Read and print logs to Console.app
            let outData = outputPipe.fileHandleForReading.readDataToEndOfFile()
            if let output = String(data: outData, encoding: .utf8), !output.isEmpty {
                print("Shell Output (\(name)):\n\(output)")
            }
            let errData = errorPipe.fileHandleForReading.readDataToEndOfFile()
            if let errOutput = String(data: errData, encoding: .utf8), !errOutput.isEmpty {
                print("Shell Errors (\(name)):\n\(errOutput)")
            }
        } else {
            // For async calls, stream stderr in the background to catch startup errors
            print("Started process: \(name)")
            DispatchQueue.global().async {
                let errData = errorPipe.fileHandleForReading.readDataToEndOfFile()
                if let errOutput = String(data: errData, encoding: .utf8), !errOutput.isEmpty {
                    print("Shell Errors (\(name)):\n\(errOutput)")
                }
            }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
