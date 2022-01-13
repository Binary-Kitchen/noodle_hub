function eventHandler(event) {
    alert(event.data)
}

document.addEventListener("DOMContentLoaded", function() {
    alert("Hello World")
    const evtSource = new EventSource("https://noodlehub.binary.kitchen/push")
    
    evtSource.onmessage = eventHandler
});