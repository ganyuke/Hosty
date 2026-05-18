using System;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Concurrent;

namespace winui_ui
{
    public class PythonBackendClient : IDisposable
    {
        private Process? _pythonProcess;
        private StreamWriter? _stdin;
        private StreamReader? _stdout;
        private int _requestIdCounter = 0;
        private bool _disposed = false;

        private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonElement>> _pendingRequests = new();

        public event EventHandler<JsonElement>? ServerAdded;
        public event EventHandler<JsonElement>? ServerRemoved;
        public event EventHandler<JsonElement>? ServerChanged;
        public event EventHandler? BackendReady;

        public event EventHandler<JsonElement>? InstallProgress;
        public event EventHandler<JsonElement>? InstallComplete;
        public event EventHandler<JsonElement>? InstallError;
        public event EventHandler<JsonElement>? ConsoleOutput;
        public event EventHandler<JsonElement>? ServerStatusChanged;
        public event EventHandler<JsonElement>? BackupComplete;
        public event EventHandler<JsonElement>? BackupSkipped;

        /// <summary>
        /// Starts the Python IPC backend process.
        /// pythonExecutable: full path to python.exe (or "python")
        /// backendScriptPath: full path to win_ipc.py
        /// projectRoot: the Hosty repo root, used as WorkingDirectory and PYTHONPATH
        /// </summary>
        public void Start(string pythonExecutable, string backendScriptPath, string projectRoot)
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = pythonExecutable,
                Arguments = $"\"{backendScriptPath}\"",
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = projectRoot,
            };

            // Set PYTHONPATH so "from hosty.xxx" imports work
            startInfo.Environment["PYTHONPATH"] = projectRoot;

            _pythonProcess = new Process { StartInfo = startInfo };
            _pythonProcess.Start();

            _stdin = _pythonProcess.StandardInput;
            _stdin.AutoFlush = true;
            _stdout = _pythonProcess.StandardOutput;

            // Start reading stdout in a background task
            Task.Run(ReadStdoutLoop);
            // Start reading stderr for diagnostics
            Task.Run(ReadStderrLoop);
        }

        private async Task ReadStderrLoop()
        {
            try
            {
                var stderr = _pythonProcess?.StandardError;
                if (stderr == null) return;

                while (!stderr.EndOfStream)
                {
                    var line = await stderr.ReadLineAsync();
                    if (!string.IsNullOrWhiteSpace(line))
                    {
                        Debug.WriteLine($"[Python stderr] {line}");
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"Stderr loop exception: {ex.Message}");
            }
        }

        private async Task ReadStdoutLoop()
        {
            try
            {
                while (_stdout != null && !_stdout.EndOfStream)
                {
                    var line = await _stdout.ReadLineAsync();
                    if (string.IsNullOrWhiteSpace(line)) continue;

                    Debug.WriteLine($"[Python stdout] {line}");

                    try
                    {
                        using var doc = JsonDocument.Parse(line);
                        var root = doc.RootElement;

                        if (root.TryGetProperty("id", out var idProp) && idProp.ValueKind != JsonValueKind.Null)
                        {
                            int reqId = idProp.GetInt32();
                            if (_pendingRequests.TryRemove(reqId, out var tcs))
                            {
                                if (root.TryGetProperty("error", out var errProp) && errProp.ValueKind != JsonValueKind.Null)
                                {
                                    tcs.TrySetException(new Exception(errProp.GetString()));
                                }
                                else if (root.TryGetProperty("result", out var resProp))
                                {
                                    tcs.TrySetResult(resProp.Clone());
                                }
                                else
                                {
                                    tcs.TrySetResult(default);
                                }
                            }
                        }
                        else if (root.TryGetProperty("event", out var eventProp))
                        {
                            var eventName = eventProp.GetString();
                            var data = root.TryGetProperty("data", out var dataProp) ? dataProp.Clone() : default;

                            switch (eventName)
                            {
                                case "ready":
                                    BackendReady?.Invoke(this, EventArgs.Empty);
                                    break;
                                case "server-added":
                                    ServerAdded?.Invoke(this, data);
                                    break;
                                case "server-removed":
                                    ServerRemoved?.Invoke(this, data);
                                    break;
                                case "server-changed":
                                    ServerChanged?.Invoke(this, data);
                                    break;
                                case "install-progress":
                                    InstallProgress?.Invoke(this, data);
                                    break;
                                case "install-complete":
                                    InstallComplete?.Invoke(this, data);
                                    break;
                                case "install-error":
                                    InstallError?.Invoke(this, data);
                                    break;
                                case "console-output":
                                    ConsoleOutput?.Invoke(this, data);
                                    break;
                                case "server-status":
                                    ServerStatusChanged?.Invoke(this, data);
                                    break;
                                case "backup-complete":
                                    BackupComplete?.Invoke(this, data);
                                    break;
                                case "backup-skipped":
                                    BackupSkipped?.Invoke(this, data);
                                    break;
                            }
                        }
                    }
                    catch (Exception ex)
                    {
                        Debug.WriteLine($"Error parsing IPC message: {ex.Message}");
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"Stdout loop exception: {ex.Message}");
            }
            finally
            {
                // Process has exited or stdout closed — fail any pending requests
                Debug.WriteLine("Python process stdout ended. Failing pending requests.");
                foreach (var kvp in _pendingRequests)
                {
                    if (_pendingRequests.TryRemove(kvp.Key, out var tcs))
                    {
                        tcs.TrySetException(new Exception("Python backend process has exited."));
                    }
                }
            }
        }

        public async Task<JsonElement> SendRequestAsync(string method, object? paramsObj = null, int timeoutMs = 30000)
        {
            if (_disposed || _pythonProcess == null || _pythonProcess.HasExited)
            {
                throw new InvalidOperationException("Python backend process is not running.");
            }

            int reqId = Interlocked.Increment(ref _requestIdCounter);
            var tcs = new TaskCompletionSource<JsonElement>();
            _pendingRequests[reqId] = tcs;

            var request = new
            {
                id = reqId,
                method = method,
                @params = paramsObj ?? new { }
            };

            var json = JsonSerializer.Serialize(request);
            Debug.WriteLine($"[IPC Send] {json}");

            try
            {
                if (_stdin != null)
                {
                    await _stdin.WriteLineAsync(json);
                    await _stdin.FlushAsync();
                }
                else
                {
                    _pendingRequests.TryRemove(reqId, out _);
                    throw new InvalidOperationException("IPC Backend not started.");
                }
            }
            catch (Exception ex)
            {
                // Write failed (pipe closed, etc)
                _pendingRequests.TryRemove(reqId, out _);
                throw new InvalidOperationException($"Failed to write to Python process: {ex.Message}", ex);
            }

            // Wait with timeout
            using var cts = new CancellationTokenSource(timeoutMs);
            try
            {
                cts.Token.Register(() =>
                {
                    if (_pendingRequests.TryRemove(reqId, out var t))
                    {
                        t.TrySetException(new TimeoutException($"IPC request '{method}' timed out after {timeoutMs}ms."));
                    }
                });

                return await tcs.Task;
            }
            catch (TimeoutException)
            {
                throw;
            }
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            // Fail any remaining requests
            foreach (var kvp in _pendingRequests)
            {
                if (_pendingRequests.TryRemove(kvp.Key, out var tcs))
                {
                    tcs.TrySetCanceled();
                }
            }

            if (_pythonProcess != null && !_pythonProcess.HasExited)
            {
                try
                {
                    _pythonProcess.Kill();
                }
                catch { }
                _pythonProcess.Dispose();
            }
        }
    }
}
