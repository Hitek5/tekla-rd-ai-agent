using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using TeklaAgent.Contracts;

namespace TeklaWorkstationHost
{
    internal static class Program
    {
        private static void Main(string[] args)
        {
            var host = new LocalToolHost(new StubTeklaFacade());
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

        public LocalToolHost(ITeklaFacade tekla)
        {
            _tekla = tekla;
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

                if (MutatingTools.Contains(tool) && string.IsNullOrWhiteSpace(context.Request.Headers["X-Agent-Approval"]))
                {
                    Audit(tool, false, "blocked_missing_approval_header");
                    await WriteJsonAsync(
                        context,
                        403,
                        new { error = "Mutating tool requires X-Agent-Approval header", tool }
                    ).ConfigureAwait(false);
                    return;
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

