using System.Text.Json;
using Sdcb.SimdPaddleOCR;

if (args.Length != 1) throw new ArgumentException("Expected the official SimdPaddleOCR models directory.");
var model = args[0];
var detPath = Path.Combine(model, "medium_det.onnx");
var recPath = Path.Combine(model, "medium_rec.onnx");
var dictPath = Path.Combine(model, "rec_keys.txt");
var options = new PaddleOcrOptions
{
    UseDirectionClassification = false,
    LineWorkerCount = 2,
    DetIntraOpThreads = 4,
};
using var all = PaddleOcrAll.Load(detPath, null, recPath, dictPath, options);
using var rec = new LazyRecognizer(recPath, dictPath);
Console.WriteLine("{\"ready\":true}");
for (string? message; (message = Console.ReadLine()) != null;)
{
    try
    {
        using var doc = JsonDocument.Parse(message);
        var request = doc.RootElement;
        var w = request.GetProperty("width").GetInt32();
        var h = request.GetProperty("height").GetInt32();
        if (w is < 1 or > 2000 || h is < 1 or > 2000)
            throw new ArgumentOutOfRangeException(nameof(message), "Invalid image dimensions.");
        var bgr = request.GetProperty("bgr").GetBytesFromBase64();
        if (bgr.Length != w * h * 3)
            throw new ArgumentException("Invalid BGR payload length.");

        Line[] lines;
        if (request.GetProperty("only_rec").GetBoolean())
        {
            var r = rec.Value.Recognize(bgr, w, h);
            lines = [new Line(r.Text, r.Score, 0, 0, w, h)];
        }
        else
        {
            lines = all.Run(bgr, w, h).Lines.Select(l =>
            {
                var b = l.Box;
                return new Line(l.Text, l.RecognitionScore,
                    Math.Min(Math.Min(b.X1, b.X2), Math.Min(b.X3, b.X4)),
                    Math.Min(Math.Min(b.Y1, b.Y2), Math.Min(b.Y3, b.Y4)),
                    Math.Max(Math.Max(b.X1, b.X2), Math.Max(b.X3, b.X4)),
                    Math.Max(Math.Max(b.Y1, b.Y2), Math.Max(b.Y3, b.Y4)));
            }).ToArray();
        }
        Console.WriteLine(JsonSerializer.Serialize(new { lines }));
    }
    catch (Exception ex)
    {
        Console.WriteLine(JsonSerializer.Serialize(new { error = ex.Message }));
    }
}

record Line(string Text, float Score, float X1, float Y1, float X2, float Y2);

sealed class LazyRecognizer(string recPath, string dictPath) : IDisposable
{
    private PaddleOcrRecognizer? _value;
    public PaddleOcrRecognizer Value => _value ??= Load();
    private PaddleOcrRecognizer Load()
    {
        using var rec = File.OpenRead(recPath);
        using var dict = File.OpenRead(dictPath);
        return new PaddleOcrRecognizer(rec, dict);
    }
    public void Dispose() => _value?.Dispose();
}
