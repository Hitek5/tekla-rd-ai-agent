using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using TeklaAgent.Contracts;

namespace TeklaWorkstationHost
{
    internal static class Program
    {
        private static void Main(string[] args)
        {
            // Shared HMAC secret; MUST match the orchestrator's APPROVAL_SECRET.
            var secret = Environment.GetEnvironmentVariable("TEKLA_AGENT_APPROVAL_SECRET");
            // This host's identity. The token is bound to a workstation_url; the host
            // rejects tokens minted for a different one, so when several hosts share
            // the secret a token leaked from host A cannot be replayed against host B.
            // Defaults to the orchestrator's default target for single-host setups.
            var workstationUrl = Environment.GetEnvironmentVariable("TEKLA_AGENT_WORKSTATION_URL")
                ?? "http://127.0.0.1:51234";
            var verifier = new ApprovalVerifier(secret, workstationUrl);
            if (!verifier.Enabled)
            {
                var why = verifier.DisabledReason == "host_secret_weak_or_default"
                    ? "TEKLA_AGENT_APPROVAL_SECRET is weak or a well-known default"
                    : "TEKLA_AGENT_APPROVAL_SECRET is not set";
                Console.WriteLine(
                    "WARNING: " + why + ". The host fails closed — ALL mutating tool calls " +
                    "will be REJECTED until you set a strong secret matching the orchestrator's " +
                    "APPROVAL_SECRET. Read-only tools still work."
                );
            }

            var host = new LocalToolHost(new StubTeklaFacade(), verifier);
            // Listen on the configured workstation URL so the bind address matches
            // the identity tokens are bound to (HttpListener prefixes need a
            // trailing slash). For a non-loopback prefix on Windows the operator
            // must grant a urlacl (netsh http add urlacl).
            var prefix = workstationUrl.EndsWith("/") ? workstationUrl : workstationUrl + "/";
            host.RunAsync(prefix).GetAwaiter().GetResult();
        }
    }

    internal sealed class LocalToolHost
    {
        private static readonly HashSet<string> MutatingTools = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "CreateBeam",
            "CreateColumn",
            "CreateRebar",
            "ModifyObject",
            "DeleteObject",
            "GenerateDrawingDraft"
        };

        // Mutating tools the StubTeklaFacade actually dispatches. Others are rejected
        // with 501 before the approval nonce is touched (see HandleAsync), until the
        // real Tekla facade implements them.
        private static readonly HashSet<string> ImplementedMutatingTools = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "CreateBeam",
            "CreateColumn"
        };

        private static readonly HashSet<string> AllowedTools = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "GetSelection",
            "QueryObjects",
            "ValidateModel",
            "DryRun",
            "CreateBeam",
            "CreateColumn",
            "CreateRebar",
            "ModifyObject",
            "DeleteObject",
            "GenerateDrawingDraft"
        };

        private readonly ITeklaFacade _tekla;
        private readonly ApprovalVerifier _verifier;

        public LocalToolHost(ITeklaFacade tekla, ApprovalVerifier verifier)
        {
            _tekla = tekla;
            _verifier = verifier;
        }

        public async Task RunAsync(string prefix)
        {
            using (var listener = new HttpListener())
            {
                listener.Prefixes.Add(prefix);
                listener.Start();
                Console.WriteLine("Tekla workstation host listening on " + prefix);

                while (true)
                {
                    var context = await listener.GetContextAsync().ConfigureAwait(false);
                    _ = Task.Run(() => HandleAsync(context));
                }
            }
        }

        private async Task HandleAsync(HttpListenerContext context)
        {
            try
            {
                var path = context.Request.Url.AbsolutePath.Trim('/');
                if (path.Equals("health", StringComparison.OrdinalIgnoreCase))
                {
                    await WriteJsonAsync(context, 200, new { status = "ok" }).ConfigureAwait(false);
                    return;
                }

                if (!path.StartsWith("tools/", StringComparison.OrdinalIgnoreCase))
                {
                    await WriteJsonAsync(context, 404, new { error = "Unknown route" }).ConfigureAwait(false);
                    return;
                }

                var tool = path.Substring("tools/".Length);
                if (!AllowedTools.Contains(tool))
                {
                    await WriteJsonAsync(context, 403, new { error = "Tool is not allowed", tool }).ConfigureAwait(false);
                    return;
                }

                // Read the RAW body bytes BEFORE verifying so the approval can be
                // bound to the actual request arguments (body_sha256). We hash the
                // raw bytes and decode as UTF-8 explicitly — never via
                // ContentEncoding, which falls back to the machine codepage for
                // non-ASCII (e.g. a Cyrillic Name) without a charset and would
                // break the hash match the orchestrator signed over UTF-8 bytes.
                var bodyBytes = await ReadBodyBytesAsync(context.Request).ConfigureAwait(false);
                var body = Encoding.UTF8.GetString(bodyBytes);

                if (MutatingTools.Contains(tool))
                {
                    // Reject not-yet-implemented mutating tools BEFORE touching the
                    // approval, so a one-time token is never consumed on a guaranteed
                    // no-op.
                    if (!ImplementedMutatingTools.Contains(tool))
                    {
                        Audit(tool, false, "not_implemented");
                        await WriteJsonAsync(
                            context, 501, new { error = "Tool not implemented", tool }
                        ).ConfigureAwait(false);
                        return;
                    }

                    var token = context.Request.Headers["X-Agent-Approval"];
                    var check = _verifier.Verify(token, tool, Sha256Hex(bodyBytes));
                    if (!check.Ok)
                    {
                        Audit(tool, false, "blocked_approval:" + check.Reason);
                        await WriteJsonAsync(
                            context,
                            403,
                            new { error = "Approval check failed", reason = check.Reason, tool }
                        ).ConfigureAwait(false);
                        return;
                    }

                    // The nonce is reserved; commit it only on a successful execution,
                    // roll back otherwise (including a dispatch exception) so the
                    // approval is not wasted on a failure.
                    ToolResult mResult;
                    try
                    {
                        mResult = Dispatch(tool, body);
                    }
                    catch
                    {
                        _verifier.RollbackNonce(check.Nonce);
                        throw;
                    }
                    if (mResult.Success)
                    {
                        _verifier.CommitNonce(check.Nonce);
                    }
                    else
                    {
                        _verifier.RollbackNonce(check.Nonce);
                    }
                    Audit(tool, mResult.Success, mResult.Message);
                    await WriteJsonAsync(context, mResult.Success ? 200 : 500, mResult).ConfigureAwait(false);
                    return;
                }

                var result = Dispatch(tool, body);
                Audit(tool, result.Success, result.Message);
                await WriteJsonAsync(context, result.Success ? 200 : 500, result).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                await WriteJsonAsync(context, 500, new { error = ex.Message }).ConfigureAwait(false);
            }
        }

        private ToolResult Dispatch(string tool, string body)
        {
            switch (tool)
            {
                case "GetSelection":
                    return _tekla.GetSelection();
                case "QueryObjects":
                    return _tekla.QueryObjects(JsonConvert.DeserializeObject<QueryObjectsRequest>(body));
                case "ValidateModel":
                    return _tekla.ValidateModel();
                case "DryRun":
                    return new ToolResult
                    {
                        Success = true,
                        Message = "Dry-run accepted by workstation host.",
                        Data = JsonConvert.DeserializeObject<object>(body)
                    };
                case "CreateBeam":
                    return _tekla.CreateBeam(JsonConvert.DeserializeObject<CreateBeamRequest>(body));
                case "CreateColumn":
                    return _tekla.CreateColumn(JsonConvert.DeserializeObject<CreateColumnRequest>(body));
                default:
                    return new ToolResult
                    {
                        Success = false,
                        Message = "Tool is declared but not implemented in this starter host: " + tool
                    };
            }
        }

        private static async Task<byte[]> ReadBodyBytesAsync(HttpListenerRequest request)
        {
            using (var ms = new MemoryStream())
            {
                await request.InputStream.CopyToAsync(ms).ConfigureAwait(false);
                return ms.ToArray();
            }
        }

        private static string Sha256Hex(byte[] data)
        {
            using (var sha = SHA256.Create())
            {
                var hash = sha.ComputeHash(data ?? new byte[0]);
                var sb = new StringBuilder(hash.Length * 2);
                foreach (var b in hash)
                {
                    sb.Append(b.ToString("x2"));
                }
                return sb.ToString();
            }
        }

        private static async Task WriteJsonAsync(HttpListenerContext context, int statusCode, object payload)
        {
            var json = JsonConvert.SerializeObject(payload);
            var bytes = Encoding.UTF8.GetBytes(json);
            context.Response.StatusCode = statusCode;
            context.Response.ContentType = "application/json; charset=utf-8";
            context.Response.ContentLength64 = bytes.Length;
            await context.Response.OutputStream.WriteAsync(bytes, 0, bytes.Length).ConfigureAwait(false);
            context.Response.OutputStream.Close();
        }

        private static readonly object _auditLock = new object();

        private static void Audit(string tool, bool success, string message)
        {
            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "TeklaAgent"
            );
            Directory.CreateDirectory(dir);
            var line = JsonConvert.SerializeObject(new
            {
                timestamp = DateTimeOffset.UtcNow.ToString("O"),
                tool,
                success,
                message
            });
            // Handlers run on concurrent Tasks; serialize the append so two writers
            // cannot hit a file-sharing violation (mirrors the nonce ledger lock).
            lock (_auditLock)
            {
                File.AppendAllText(Path.Combine(dir, "mcp-audit.jsonl"), line + Environment.NewLine, Encoding.UTF8);
            }
        }
    }

    internal struct ApprovalCheck
    {
        public bool Ok;
        public string Reason;
        public string Nonce;

        public static ApprovalCheck Fail(string reason)
        {
            return new ApprovalCheck { Ok = false, Reason = reason };
        }

        public static ApprovalCheck Pass(string reason, string nonce)
        {
            return new ApprovalCheck { Ok = true, Reason = reason, Nonce = nonce };
        }
    }

    /// <summary>
    /// Independently verifies the orchestrator's HMAC approval token on the
    /// workstation, using the same shared secret. This is defence in depth: even
    /// if the orchestrator were bypassed (a direct call to the host), a mutating
    /// call still needs a token whose signature, expiry, target tool, argument
    /// binding (body_sha256 of the raw request bytes) and single-use nonce all
    /// check out here.
    ///
    /// The secret must be strong: a weak/well-known value is refused (verification
    /// stays disabled, so mutating calls fail closed), mirroring the orchestrator.
    /// </summary>
    internal sealed class ApprovalVerifier
    {
        private static readonly HashSet<string> WeakSecrets = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "change-me-please-set-a-32-char-secret",
            "change-me",
            "changeme",
            "secret",
            "local-dev-key",
        };

        private readonly byte[] _secret;
        private readonly string _workstationUrl;
        private readonly object _lock = new object();
        private readonly HashSet<string> _seenNonces = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _reservedNonces = new HashSet<string>(StringComparer.Ordinal);
        private readonly string _noncePath;

        public string DisabledReason { get; private set; }

        public ApprovalVerifier(string secret, string workstationUrl = "")
        {
            _workstationUrl = workstationUrl ?? string.Empty;
            if (string.IsNullOrEmpty(secret))
            {
                _secret = null;
                DisabledReason = "host_secret_not_configured";
            }
            else if (secret.Length < 16
                || WeakSecrets.Contains(secret)
                || secret.StartsWith("change-me", StringComparison.OrdinalIgnoreCase))
            {
                // Refuse to verify with a weak/default secret — otherwise a local
                // caller could forge tokens. Fail closed: leave verification off.
                _secret = null;
                DisabledReason = "host_secret_weak_or_default";
            }
            else
            {
                _secret = Encoding.UTF8.GetBytes(secret);
                DisabledReason = null;
            }

            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "TeklaAgent"
            );
            Directory.CreateDirectory(dir);
            _noncePath = Path.Combine(dir, "consumed-nonces.log");
            if (File.Exists(_noncePath))
            {
                foreach (var line in File.ReadAllLines(_noncePath))
                {
                    var trimmed = line.Trim();
                    if (trimmed.Length > 0)
                    {
                        _seenNonces.Add(trimmed);
                    }
                }
            }
        }

        public bool Enabled
        {
            get { return _secret != null; }
        }

        // Host-side single-use ledger with reserve/commit/rollback (mirrors the
        // orchestrator). The nonce is RESERVED at verification and only COMMITTED
        // (persisted as spent) after the tool actually executes successfully, so a
        // failed/unimplemented dispatch does not waste the one-time approval. A
        // concurrent duplicate that hits the host directly is still blocked because
        // reserve is atomic. Returns false if already reserved or spent.
        public bool ReserveNonce(string nonce)
        {
            lock (_lock)
            {
                if (_seenNonces.Contains(nonce) || _reservedNonces.Contains(nonce))
                {
                    return false;
                }
                _reservedNonces.Add(nonce);
                return true;
            }
        }

        public void CommitNonce(string nonce)
        {
            lock (_lock)
            {
                _reservedNonces.Remove(nonce);
                if (_seenNonces.Contains(nonce))
                {
                    return;
                }
                _seenNonces.Add(nonce);
                File.AppendAllText(_noncePath, nonce + Environment.NewLine, Encoding.UTF8);
            }
        }

        public void RollbackNonce(string nonce)
        {
            lock (_lock)
            {
                _reservedNonces.Remove(nonce);
            }
        }

        public ApprovalCheck Verify(string token, string expectedTool, string requestBodySha256)
        {
            if (!Enabled)
            {
                // Fail closed: without a strong shared secret we cannot verify any
                // approval, so a mutating call must be rejected rather than waved
                // through. The operator must set a strong TEKLA_AGENT_APPROVAL_SECRET.
                return ApprovalCheck.Fail(DisabledReason ?? "host_secret_not_configured");
            }
            if (string.IsNullOrWhiteSpace(token))
            {
                return ApprovalCheck.Fail("missing_token");
            }

            var parts = token.Split('.');
            if (parts.Length != 2)
            {
                return ApprovalCheck.Fail("malformed_token");
            }

            byte[] payloadBytes;
            try
            {
                payloadBytes = Base64UrlDecode(parts[0]);
            }
            catch (FormatException)
            {
                return ApprovalCheck.Fail("malformed_payload");
            }

            string expectedSig;
            using (var hmac = new HMACSHA256(_secret))
            {
                expectedSig = Base64UrlEncode(hmac.ComputeHash(payloadBytes));
            }
            if (!FixedTimeEquals(expectedSig, parts[1]))
            {
                return ApprovalCheck.Fail("bad_signature");
            }

            JObject claims;
            try
            {
                claims = JObject.Parse(Encoding.UTF8.GetString(payloadBytes));
            }
            catch (JsonException)
            {
                return ApprovalCheck.Fail("bad_claims");
            }

            var exp = claims.Value<long?>("exp") ?? 0;
            if (DateTimeOffset.UtcNow.ToUnixTimeSeconds() >= exp)
            {
                return ApprovalCheck.Fail("expired");
            }
            if (!string.Equals(claims.Value<string>("tool"), expectedTool, StringComparison.Ordinal))
            {
                return ApprovalCheck.Fail("tool_mismatch");
            }

            // Argument binding: the orchestrator sends the exact canonical body
            // the token was minted for, and signs its SHA-256 into body_sha256.
            // We hash the raw bytes we received and compare — so a token approved
            // for one CreateBeam cannot be reused with different arguments, even
            // on a direct call that bypasses the orchestrator. No cross-language
            // JSON re-serialisation is involved.
            var boundHash = claims.Value<string>("body_sha256");
            if (string.IsNullOrEmpty(boundHash) || !FixedTimeEquals(boundHash, requestBodySha256))
            {
                return ApprovalCheck.Fail("args_mismatch");
            }

            // Workstation binding: reject a token minted for a different host. With
            // several hosts sharing the secret, this stops a token leaked from host
            // A being posted directly to host B (which the orchestrator's own URL
            // check cannot see). An empty claim is treated as unbound and rejected,
            // never as "matches any host".
            var boundUrl = claims.Value<string>("workstation_url");
            if (string.IsNullOrEmpty(boundUrl)
                || !string.Equals(boundUrl, _workstationUrl, StringComparison.OrdinalIgnoreCase))
            {
                return ApprovalCheck.Fail("workstation_mismatch");
            }

            // Single-use: RESERVE the nonce (atomic; blocks concurrent duplicates
            // and direct-to-host replays). The caller commits it only after the
            // tool executes successfully, or rolls back on failure.
            var nonce = claims.Value<string>("nonce");
            if (string.IsNullOrEmpty(nonce))
            {
                return ApprovalCheck.Fail("missing_nonce");
            }
            if (!ReserveNonce(nonce))
            {
                return ApprovalCheck.Fail("already_used");
            }

            return ApprovalCheck.Pass("approved", nonce);
        }

        private static byte[] Base64UrlDecode(string input)
        {
            var s = input.Replace('-', '+').Replace('_', '/');
            switch (s.Length % 4)
            {
                case 2: s += "=="; break;
                case 3: s += "="; break;
            }
            return Convert.FromBase64String(s);
        }

        private static string Base64UrlEncode(byte[] input)
        {
            return Convert.ToBase64String(input).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        }

        // Constant-time string comparison (net48 lacks CryptographicOperations).
        private static bool FixedTimeEquals(string a, string b)
        {
            if (a.Length != b.Length)
            {
                return false;
            }
            var diff = 0;
            for (var i = 0; i < a.Length; i++)
            {
                diff |= a[i] ^ b[i];
            }
            return diff == 0;
        }
    }

    internal interface ITeklaFacade
    {
        ToolResult GetSelection();
        ToolResult QueryObjects(QueryObjectsRequest request);
        ToolResult ValidateModel();
        ToolResult CreateBeam(CreateBeamRequest request);
        ToolResult CreateColumn(CreateColumnRequest request);
    }

    internal sealed class StubTeklaFacade : ITeklaFacade
    {
        public ToolResult GetSelection()
        {
            return new ToolResult
            {
                Success = true,
                Message = "Stub selection returned. Replace StubTeklaFacade with Tekla Open API adapter.",
                Data = new[] { new { guid = "stub-guid", type = "Beam", profile = "HEA300" } }
            };
        }

        public ToolResult QueryObjects(QueryObjectsRequest request)
        {
            return new ToolResult
            {
                Success = true,
                Message = "Stub query returned.",
                Data = new { request, count = 0, objects = new object[0] }
            };
        }

        public ToolResult ValidateModel()
        {
            return new ToolResult
            {
                Success = true,
                Message = "Stub validation passed.",
                Data = new { warnings = new object[0] }
            };
        }

        public ToolResult CreateBeam(CreateBeamRequest request)
        {
            return new ToolResult
            {
                Success = true,
                Message = "Stub beam creation accepted. No Tekla model was modified.",
                Data = new { request, createdGuid = "stub-created-beam" },
                Warnings = new List<string> { "Wire this method to Tekla.Structures.Model.Beam before pilot use." }
            };
        }

        public ToolResult CreateColumn(CreateColumnRequest request)
        {
            return new ToolResult
            {
                Success = true,
                Message = "Stub column creation accepted. No Tekla model was modified.",
                Data = new { request, createdGuid = "stub-created-column" },
                Warnings = new List<string> { "Wire this method to Tekla.Structures.Model.Beam before pilot use." }
            };
        }
    }
}

