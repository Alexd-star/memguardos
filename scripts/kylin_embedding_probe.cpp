#include <algorithm>
#include <iostream>
#include <string>

#include <kylin-ai/coreai/embedding/embedding.h>

namespace {

void print_result(const char* label, EmbeddingResult* result) {
    std::cout << label << ".present=" << (result ? "true" : "false") << '\n';
    if (!result) {
        return;
    }
    const int error_code = embedding_result_get_error_code(result);
    const char* error_message = embedding_result_get_error_message(result);
    const int length = embedding_result_get_vector_length(result);
    float* data = embedding_result_get_vector_data(result);

    std::cout << label << ".error_code=" << error_code << '\n';
    std::cout << label << ".error_message=" << (error_message ? error_message : "") << '\n';
    std::cout << label << ".length=" << length << '\n';
    std::cout << label << ".sample=";
    const int sample_count = std::min(length, 5);
    for (int i = 0; i < sample_count && data; ++i) {
        if (i > 0) {
            std::cout << ',';
        }
        std::cout << data[i];
    }
    std::cout << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    const char* text = argc > 1 ? argv[1] : "openKylin agent memory";

    TextEmbeddingSession* session = text_embedding_create_session();
    std::cout << "session.created=" << (session ? "true" : "false") << '\n';
    if (!session) {
        return 2;
    }

    const int init_code = text_embedding_init_session(session);
    std::cout << "session.init_code=" << init_code << '\n';

    char* model_info = nullptr;
    const bool model_ok = text_embedding_get_model_info(session, &model_info);
    std::cout << "model_info.ok=" << (model_ok ? "true" : "false") << '\n';
    if (model_info) {
        std::cout << "model_info.raw=" << model_info << '\n';
        embedding_model_info_destroy(model_info);
    }

    EmbeddingResult* result = nullptr;
    const bool embedding_ok = text_embedding(session, text, &result);
    std::cout << "embedding.call_ok=" << (embedding_ok ? "true" : "false") << '\n';
    print_result("embedding", result);
    embedding_result_destroy(&result);

    text_embedding_destroy_session(&session);
    std::cout << "session.destroyed=true\n";
    return 0;
}
