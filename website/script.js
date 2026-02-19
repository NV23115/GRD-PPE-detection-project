// Optional interactivity for website
// Placeholder: toggle sections, future enhancements

console.log("PPE Detection Implementation Guide Loaded");

// Example: smooth scroll for nav links
document.querySelectorAll('nav a').forEach(link => {
    link.addEventListener('click', function(e){
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        target.scrollIntoView({ behavior: 'smooth' });
    });
});