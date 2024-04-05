$(document).ready(function(){

    $('#secretmessage').hover(
        function(){
            $('.help').text(
                "Testyyy."
            );
            $('.help').show();
        },
        function(){
            $('.help').hide();
        }
    );
