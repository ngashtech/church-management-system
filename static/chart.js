// Function to fetch data and render charts
async function loadChurchCharts() {
    const response = await fetch('/api/get_data'); // You'll need this route in app.py
    const records = await response.json();

    const labels = records.map(r => `Sat ${r.saturday_no}`);
    
    // Step 2: Compounding the data for the graph
    const menData = records.map(r => r.men);
    const womenData = records.map(r => r.women + r.men); // Red + Green layer
    const youthData = records.map(r =>  r.youth + r.women + r.men); // + Yellow layer
    const childrenData = records.map(r =>  r.children + r.youth + r.women + r.men); // + Blue layer (Total)
    const totalData = records.map(r =>  r.children + r.women + r.men);

    // POPULATION CHART (Compounding)
    const ctxPop = document.getElementById('popChart').getContext('2d');
    new Chart(ctxPop, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                { label: 'Men (Base)', data: menData, borderColor: 'red', backgroundColor: 'rgba(196, 18, 18, 0.1)', fill: true },
                { label: 'Women', data: womenData, borderColor: 'green', backgroundColor: 'rgba(21, 179, 21, 0.1)', fill: true },
                { label: 'Youth', data: youthData, borderColor: 'yellow', backgroundColor: 'rgba(207, 207, 20, 0.1)', fill: true },
                { label: 'Children ', data: childrenData, borderColor: 'blue', backgroundColor: 'rgba(24, 24, 153, 0.1)', fill: true },
                {label: 'Total Population', data: totalData, borderColor: 'grey', backgroundColor: 'rgba(128, 128, 128, 0.1)', fill: false }  
            ]
        },
        options: { responsive: true, plugins: { title: { display: true, text: 'Compounded Population Trend' } } }
    });

    // FINANCE CHART (Step 3: Brown & Orange)
    const ctxFin = document.getElementById('financeChart').getContext('2d');
    new Chart(ctxFin, {
        type: 'line',
        data: {
            labels: records.map(r => r.total_attendance), // X-axis is Population
            datasets: [
                { label: 'Tithe', data: records.map(r => r.tithe), borderColor: 'brown', showLine: records.length > 1 },
                { label: 'Offering', data: records.map(r => r.offering), borderColor: 'orange', showLine: records.length > 1 }
            ]
        },
        options: {
            scales: {
                x: { title: { display: true, text: 'Church Population' } },
                y: { title: { display: true, text: 'Amount ($)' } }
            }
        }
    });
}

// Call the function when the page loads
window.onload = loadChurchCharts;