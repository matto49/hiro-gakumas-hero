package extplugin

import (
	"encoding/json"
	"os/exec"
	"reflect"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

type panelTestHost struct {
	*plugintest.Host
	work    []func() func()
	session uint64
}

func (h *panelTestHost) PanelSession() uint64  { return h.session }
func (h *panelTestHost) Go(work func() func()) { h.work = append(h.work, work) }

func (h *panelTestHost) response(t *testing.T) func() {
	t.Helper()
	if len(h.work) != 1 {
		t.Fatalf("expected exactly one request in flight, got %d", len(h.work))
	}
	work := h.work[0]
	h.work = nil
	return work()
}

// Run the real client against a recorded stdio exchange without a subprocess.
type panelTestPipe struct {
	client     *client
	calls      []rpcMsg
	failRender bool
}

func (p *panelTestPipe) Write(line []byte) (int, error) {
	var request rpcMsg
	if err := json.Unmarshal(line, &request); err != nil {
		return 0, err
	}
	p.calls = append(p.calls, request)
	state, err := json.Marshal(len(p.calls))
	if err != nil {
		return 0, err
	}
	interval := 1000
	reply := rendered{State: state}
	if request.Method == "panel.render" {
		reply.RefreshMS = &interval
		reply.Tree = &Node{Type: "column", Children: []Node{
			{Type: "input", ID: "name", Placeholder: "Enter name"},
			{Type: "select", ID: "level", Value: "alpha", Options: []string{"alpha", "beta", "gamma"}},
			{Type: "button", ID: "apply", Text: "Apply choice"},
		}}
	}
	result, err := json.Marshal(reply)
	if err != nil {
		return 0, err
	}
	response := rpcMsg{Result: result}
	if request.Method == "panel.render" && p.failRender {
		response.Error = &rpcError{Message: "refresh failed"}
	}
	id, _ := toInt(request.ID)
	p.client.mu.Lock()
	waiter := p.client.waiting[id]
	delete(p.client.waiting, id)
	p.client.mu.Unlock()
	waiter <- response
	return len(line), nil
}

func (*panelTestPipe) Close() error { return nil }

func setupEditablePanel(t *testing.T) (*Ext, *panelTestHost, *panelTestPipe, *ui.Tester) {
	t.Helper()
	h := &panelTestHost{Host: newHost(), session: 1}
	pipe := &panelTestPipe{}
	pipe.client = &client{cmd: &exec.Cmd{}, stdin: pipe, waiting: map[int64]chan rpcMsg{}}
	e := &Ext{m: &Manifest{Name: "Demo"}, c: pipe.client, screens: map[string]*screen{}}
	t.Cleanup(e.Close)
	uiTest := ui.NewTester(plugintest.PanelView(func(c *ui.Context) {
		e.draw(c, h, "panel-demo", "panel.render", map[string]any{"route": "demo"})
	}), 520, 600)
	h.response(t)()
	uiTest.Frame()
	h.At = h.At.Add(time.Second)
	uiTest.Frame()
	return e, h, pipe, uiTest
}

func submitPanelName(t *testing.T, uiTest *ui.Tester) {
	t.Helper()
	input, ok := uiTest.AccessByRole(ui.RoleTextField, "")
	if !ok || input.Placeholder != "Enter name" {
		t.Fatal("text input is missing")
	}
	uiTest.ClickAt(input.Bounds.X+input.Bounds.W/2, input.Bounds.Y+input.Bounds.H/2)
	uiTest.Type("edited")
	uiTest.Key(0, ui.KeyEnter)
}

type panelEvent struct {
	ID    string
	Value string
	Route string
	State int
}

func observedPanelEvents(t *testing.T, pipe *panelTestPipe) []panelEvent {
	t.Helper()
	var events []panelEvent
	for _, call := range pipe.calls {
		if call.Method == "event" {
			var event panelEvent
			if err := json.Unmarshal(call.Params, &event); err != nil {
				t.Fatal(err)
			}
			events = append(events, event)
		}
	}
	return events
}

func TestNativePanelEditsDuringRefreshAreSentInOrder(t *testing.T) {
	e, h, pipe, uiTest := setupEditablePanel(t)
	submitPanelName(t, uiTest)
	click := func(label string) {
		t.Helper()
		if err := uiTest.Click(label); err != nil {
			t.Fatal(err)
		}
	}
	click("alpha")
	click("beta")
	click("beta")
	click("gamma")
	button, ok := uiTest.Find("Apply choice")
	if !ok {
		t.Fatal("button disappeared during refresh")
	}
	uiTest.ClickAt(button.X+button.W/2, button.Y+button.H/2)
	if len(pipe.calls) != 1 {
		t.Fatal("an edit was sent before the refresh responded")
	}
	h.response(t)() // Refresh completes and starts the first event.
	uiTest.Frame()
	click("gamma")
	click("alpha")
	for range 4 {
		h.response(t)()
	}
	s := e.screen("panel-demo")
	if name := *s.v.input("panel-demo/0/name", ""); name != "edited" {
		t.Fatalf("refresh overwrote text input: %q", name)
	}
	if len(h.work) != 0 {
		t.Fatal("extra work was queued")
	}
	uiTest.Frame()
	click("Apply choice")
	h.response(t)()
	want := []panelEvent{
		{"name", "edited", "demo", 2},
		{"level", "beta", "demo", 3},
		{"level", "gamma", "demo", 4},
		{"level", "alpha", "demo", 5},
		{"apply", "", "demo", 6},
	}
	if got := observedPanelEvents(t, pipe); !reflect.DeepEqual(got, want) {
		t.Fatalf("events = %+v, want %+v", got, want)
	}
}

func TestNativePanelRefreshFailureStillSendsQueuedEdit(t *testing.T) {
	e, h, pipe, uiTest := setupEditablePanel(t)
	submitPanelName(t, uiTest)
	pipe.failRender = true
	h.response(t)()
	h.response(t)()
	if got, want := observedPanelEvents(t, pipe), []panelEvent{{"name", "edited", "demo", 1}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("events = %+v, want %+v", got, want)
	}
	if len(h.work) != 0 || e.screen("panel-demo").err != "refresh failed" {
		t.Fatal("the refresh error should remain visible with no extra requests")
	}
}

func TestReopenedLivePanelWaitsForCurrentView(t *testing.T) {
	_, h, _, panel := setupEditablePanel(t)
	h.response(t)()
	panel.SetWindowHidden(true)
	h.session++
	panel.Frame()
	if len(h.work) != 0 {
		t.Fatal("a hidden window requested a fresh panel")
	}
	panel.SetWindowHidden(false)
	panel.Frame()
	if panel.HasText("Apply choice") {
		t.Fatal("reopening exposed the old view before the plugin responded")
	}
	h.response(t)()
	panel.Frame()
	if !panel.HasText("Apply choice") {
		t.Fatal("the current view was not rendered after reopening")
	}
}

func TestNativePanelDropsQueuedEditsAfterSessionEnds(t *testing.T) {
	for _, mode := range []string{"close panel", "switch panel", "unload plugin"} {
		t.Run(mode, func(t *testing.T) {
			e, h, pipe, uiTest := setupEditablePanel(t)
			submitPanelName(t, uiTest)
			reply := h.response(t)
			switch mode {
			case "close panel":
				h.session = 0
			case "switch panel":
				h.session++
			case "unload plugin":
				e.Close()
			}
			reply()
			s := e.screen("panel-demo")
			if len(h.work) != 0 || len(s.edits) != 0 || s.tree != nil || s.state != nil || len(s.v.inputs) != 0 {
				t.Fatal("old panel session kept its edits, input, or response")
			}
			if got := observedPanelEvents(t, pipe); len(got) != 0 {
				t.Fatalf("stale edit was sent: %+v", got)
			}
		})
	}
}
