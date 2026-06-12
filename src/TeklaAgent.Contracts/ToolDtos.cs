using System.Collections.Generic;

namespace TeklaAgent.Contracts
{
    public sealed class Point3D
    {
        public double X { get; set; }
        public double Y { get; set; }
        public double Z { get; set; }
    }

    public sealed class CreateBeamRequest
    {
        public Point3D Start { get; set; }
        public Point3D End { get; set; }
        public string Profile { get; set; }
        public string Material { get; set; }
        public string Class { get; set; }
        public string Name { get; set; }
    }

    public sealed class CreateColumnRequest
    {
        public Point3D BasePoint { get; set; }
        public double Height { get; set; }
        public string Profile { get; set; }
        public string Material { get; set; }
        public string Class { get; set; }
        public string Name { get; set; }
    }

    public sealed class QueryObjectsRequest
    {
        public string ObjectType { get; set; }
        public string Name { get; set; }
        public string Profile { get; set; }
        public string Material { get; set; }
        public int Limit { get; set; }
    }

    public sealed class ModifyObjectRequest
    {
        public string Guid { get; set; }
        public string Profile { get; set; }
        public string Material { get; set; }
        public string Class { get; set; }
        public Point3D NewStart { get; set; }
        public Point3D NewEnd { get; set; }
    }

    public sealed class DeleteObjectRequest
    {
        public string Guid { get; set; }
    }

    public sealed class ToolResult
    {
        public bool Success { get; set; }
        public string Message { get; set; }
        public object Data { get; set; }
        public IList<string> Warnings { get; set; }

        public ToolResult()
        {
            Warnings = new List<string>();
        }
    }
}

