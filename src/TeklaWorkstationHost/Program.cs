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
            var verifier = new ApprovalVerifier(secret);
            if (!verifier.Enabled)
            {
                Console.WriteLine(
                    "WARNING: TEKLA_AGENT_APPROVAL_SECRET not set. The host fails closed — " +
                    "ALL mutating tool calls will be REJECTED until you set it to the same " +
                    "value as the orchestrator's APPROVAL_SECRET. Read-only tools still work."
                );
            }

            var host = new LocalToolHost(new StubTeklaFacade(), verifier);
            host.RunAsync("http://127.0.0.1:51234/").GetAwaiter().GetResult();
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

                if (MutatingTools.Contains(tool))
                {
                    var token = context.Request.Headers["X-Agent-Approval"];
                    var check = _verifier.Verify(token, tool);
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
                }

                var body = await ReadBodyAsync(context.Request).ConfigureAwait(false);
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

        private static async Task<string> ReadBodyAsync(HttpListenerRequest request)
        {
            using (var reader = new StreamReader(request.InputStream, request.ContentEncoding))
            {
                return await reader.ReadToEndAsync().ConfigureAwait(false);
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
            File.AppendAllText(Path.Combine(dir, "mcp-audit.jsonl"), line + Environment.NewLine, Encoding.UTF8);
        }
    }

    internal struct ApprovalCheck
    {
        public bool Ok;
        public string Reason;

        public static ApprovalCheck Fail(string reason)
        {
            return new ApprovalCheck { Ok = false, Reason = reason };
        }

        public static ApprovalCheck Pass(string reason)
        {
            return new ApprovalCheck { Ok = true, Reason = reason };
        }
    }

    /// <summary>
    /// Independently verifies the orchestrator's HMAC approval token on the
    /// workstation, using the same shared secret. This is defence in depth: even
    /// if the orchestrator were compromised or bypassed, a mutating call still
    /// needs a token whose signature, expiry and target tool check out here.
    ///
    /// The orchestrator remains authoritative for argument binding and single-use
    /// (replay) — the host deliberately does not recompute the args hash, because
    /// canonical JSON across Python and .NET is brittle. Signature + expiry + tool
    /// match is the high-value, low-risk subset to enforce locally.
    /// </summary>
    internal sealed class ApprovalVerifier
    {
        private readonly byte[] _secret;

        public ApprovalVerifier(string secret)
        {
            _secret = string.IsNullOrEmpty(secret) ? null : Encoding.UTF8.GetBytes(secret);
        }

        public bool Enabled
        {
            get { return _secret != null; }
        }

        public ApprovalCheck Verify(string token, string expectedTool)
        {
            if (!Enabled)
            {
                // Fail closed: without the shared secret we cannot verify any
                // approval, so a mutating call must be rejected rather than waved
                // through. The operator must set TEKLA_AGENT_APPROVAL_SECRET.
                return ApprovalCheck.Fail("host_secret_not_configured");
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

            return ApprovalCheck.Pass("approved");
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

